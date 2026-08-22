"""BLE GATT 服务端（bless）—— 广播 + 特征值读写通知，桥接协议层与配网服务。

特征值：
- DEVICE_INFO(read)  返回裸 JSON 设备身份。
- REQUEST(write)     手机下发分帧命令；喂入 MessageDecoder，成帧后异步派发给 ProvisioningService。
- STATUS(notify)     下发分帧状态/事件（service 经 notify 回调）。

run as root（ai-dhlr-ble.service）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from bless import (
    BlessGATTCharacteristic,
    BlessServer,
    GATTAttributePermissions,
    GATTCharacteristicProperties,
)
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType, MessageType
from dbus_next.message import Message

from . import protocol as P
from .network_applier import current_network_status

logger = logging.getLogger(__name__)

# DEVICE_INFO 网络状态缓存的刷新间隔（秒）。current_network_status 同步跑 nmcli/ip，
# 不能在读回调里现查（阻塞事件循环/超时 → BlueZ 回 0x0E），故后台定时刷新、读时取缓存。
NETWORK_REFRESH_SEC = 3

# STATUS 通知最终会转换成 D-Bus PropertiesChanged。图片预览等大消息可能拆成上百块；
# 若在同一事件循环 tick 内连续写入，dbus-next 的非阻塞 socket 可能因发送缓冲区耗尽抛
# BlockingIOError(EAGAIN)，随后把整条 GATT D-Bus 连接 finalize，形成「进程仍在、特征值全读
# 失败 0x0E」的假活状态。串行化每条消息并轻微限速，给 BlueZ/D-Bus 留出排空时间。
# 首阶段性能优化从 15ms 保守降到 10ms；继续下调前须在真机连续大图传输下验证无 EAGAIN。
NOTIFY_CHUNK_INTERVAL_SEC = 0.010

# 广播看门狗：轮询间隔与去抖跳数。连续 N 跳都停滞才恢复——单跳判定可能落在
# 连接建立窗口内（见 _any_central_connected），去抖进一步压缩误动作概率。
WATCHDOG_INTERVAL_SEC = 5
WATCHDOG_STALLED_TICKS = 2
WATCHDOG_DBUS_TIMEOUT_SEC = 2


class GattServer:
    def __init__(
        self,
        identity: dict,
        name: str,
        mtu: int = P.DEFAULT_MTU,
    ) -> None:
        self._identity = identity
        self._name = name
        self._mtu = mtu
        self._decoder = P.MessageDecoder()
        self._seq = 0
        self._server: Optional[BlessServer] = None
        self._service = None  # ProvisioningService，由 bind_service 注入
        # 派发的 handle 任务引用集合：防被 GC（"Task was destroyed but it is pending!"）+ 丢失异常。
        self._inflight: set = set()
        # 同一 STATUS 特征值承载一条连续字节流，两个消息的分块绝不能交错；锁同时承担 D-Bus
        # 背压，避免图片通知洪峰填满 dbus-next 的非阻塞发送缓冲区。
        self._notify_lock = asyncio.Lock()
        # DEVICE_INFO 的网络状态缓存：后台任务 _refresh_network_loop 定时刷新，_read 直接读，
        # 不在读回调里现查 nmcli/ip（会阻塞 bless 事件循环 → BlueZ 回 0x0E Unlikely Error）。
        self._network: Optional[dict] = None
        # 看门狗查询使用独立的 system-bus 连接，不能与 bless 的 GATT 注册/通知共用发送队列。
        self._watchdog_bus: Optional[MessageBus] = None
        # dbus-next 遇到 EAGAIN 会 finalize bless 的 bus，但不会让业务主循环退出。监控该连接，
        # 一旦失效便使进程非零退出，由 systemd Restart=always 重新注册完整 GATT 应用。
        self._fatal_event = asyncio.Event()
        self._fatal_error: Optional[BaseException] = None
        self._background_tasks: set[asyncio.Task] = set()

    def bind_service(self, service) -> None:
        self._service = service

    # ---------------------------- bless 回调 ---------------------------- #
    def _read(self, characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
        if str(characteristic.uuid) == P.DEVICE_INFO_UUID:
            # 读回调必须瞬间返回且永不抛异常：bless 在事件循环里同步调用本函数，若在此现查
            # current_network_status（同步跑 nmcli/ip）会阻塞循环或超时 → BlueZ 回 0x0E(Unlikely
            # Error)。网络状态取后台定时刷新的缓存 self._network（见 _refresh_network_loop）。
            try:
                return bytearray(P.build_device_info(**self._identity, network=self._network))
            except Exception as e:  # noqa: BLE001
                logger.warning("DEVICE_INFO 构造失败，返回无网络兜底: %s", e)
                return bytearray(P.build_device_info(**self._identity))
        return bytearray(b"")

    def _write(self, characteristic: BlessGATTCharacteristic, value: bytearray, **kwargs) -> None:
        if str(characteristic.uuid) != P.REQUEST_UUID:
            return
        logger.info(f"[write] 收到 {len(value)} 字节写入 REQUEST")
        for seq, obj in self._decoder.feed(bytes(value)):
            logger.info(f"[write] 解析出帧 seq={seq} obj_keys={list(obj.keys())} cmd={obj.get('cmd')}")
            if self._service is not None:
                try:
                    task = asyncio.create_task(self._service.handle(seq, obj))
                    self._inflight.add(task)
                    task.add_done_callback(self._inflight.discard)
                except RuntimeError as e:
                    logger.error(f"[write] 派发失败(无事件循环?): {e}")

    # ---------------------------- 状态通知 ---------------------------- #
    async def notify(self, obj: dict) -> None:
        """分帧并通过 STATUS 特征值下发通知（best-effort）。

        bless 的 update_value(service_uuid, char_uuid) 是「读特征值当前值再通知」，
        故需先把每块 chunk 写入特征值 .value，再 update_value 触发 PropertiesChanged。
        """
        async with self._notify_lock:
            if self._server is None:
                return
            try:
                char = self._server.get_characteristic(P.STATUS_UUID)
                if char is None:
                    logger.warning("[notify] STATUS 特征值未找到，无法通知")
                    return
                self._seq = (self._seq + 1) & 0xFFFF
                chunks = P.encode_message(self._seq, obj, self._mtu)
                logger.info(f"[notify] seq={self._seq} event={obj.get('event')} 分 {len(chunks)} 块")
                for index, chunk in enumerate(chunks):
                    char.value = bytearray(chunk)
                    self._server.update_value(P.SERVICE_UUID, P.STATUS_UUID)
                    if index + 1 < len(chunks):
                        await asyncio.sleep(NOTIFY_CHUNK_INTERVAL_SEC)
            except Exception as e:  # noqa: BLE001
                logger.warning("notify 失败: %s", e)

    # ---------------------------- 生命周期 ---------------------------- #
    async def start(self) -> None:
        server = BlessServer(name=self._name)
        self._server = server
        server.read_request_func = self._read
        server.write_request_func = self._write

        await server.add_new_service(P.SERVICE_UUID)

        # 预取一次网络状态填缓存，兼作 DEVICE_INFO 初始值；之后由后台任务定时刷新。
        await self._refresh_network()

        await server.add_new_characteristic(
            P.SERVICE_UUID, P.DEVICE_INFO_UUID,
            GATTCharacteristicProperties.read,
            bytearray(P.build_device_info(**self._identity, network=self._network)),
            GATTAttributePermissions.readable,
        )
        await server.add_new_characteristic(
            P.SERVICE_UUID, P.REQUEST_UUID,
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.write_without_response,
            bytearray(b""),
            GATTAttributePermissions.writeable,
        )
        await server.add_new_characteristic(
            P.SERVICE_UUID, P.STATUS_UUID,
            GATTCharacteristicProperties.notify,
            bytearray(b""),
            GATTAttributePermissions.readable,
        )

        await server.start()
        logger.info("BLE 配网服务已广播: name=%s advertising=%s", self._name, await server.is_advertising())
        # 看门狗：bless 广告停滞(停广播且无连接)时主动 stop()+start() 重广播
        self._track_background_task(self._advertising_watchdog(), "ble-advertising-watchdog")
        # DEVICE_INFO 网络状态缓存定时刷新（读回调不现查，见 _read 说明）
        self._track_background_task(self._refresh_network_loop(), "ble-network-refresh")
        # bless/dbus-next 连接若被 finalize，必须退出进程让 systemd 恢复，不能继续假活。
        self._track_background_task(self._monitor_gatt_bus(), "ble-gatt-bus-monitor")

    def _track_background_task(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _monitor_gatt_bus(self) -> None:
        """监控 bless 的 D-Bus 连接；连接失效时触发主协程退出，由 systemd 自愈。"""
        bus = getattr(self._server, "bus", None)
        if bus is None:
            error: BaseException = RuntimeError("bless 未提供 GATT D-Bus 连接")
        else:
            try:
                await bus.wait_for_disconnect()
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # dbus-next 会把 socket 写异常传给 disconnect future
                error = exc
            else:
                error = RuntimeError("GATT D-Bus 连接已断开")
        self._fatal_error = error
        logger.error("GATT D-Bus 连接失效，退出进程交由 systemd 重启: %s", error)
        self._fatal_event.set()

    async def wait_until_failed(self) -> None:
        """阻塞到 GATT 基础连接失效，然后抛错使 systemd 看到非零退出。"""
        await self._fatal_event.wait()
        error = self._fatal_error or RuntimeError("GATT 服务失效")
        raise RuntimeError("GATT 服务基础连接已失效") from error

    async def _refresh_network(self) -> None:
        """刷新一次网络状态缓存：nmcli/ip 子进程丢线程池跑，不阻塞事件循环。失败沿用旧缓存。"""
        try:
            self._network = await asyncio.to_thread(current_network_status)
        except Exception as e:  # noqa: BLE001
            logger.warning("刷新网络状态失败，沿用旧缓存: %s", e)

    async def _refresh_network_loop(self) -> None:
        """后台定时刷新 DEVICE_INFO 的网络状态缓存。

        current_network_status 同步跑 nmcli/ip，绝不能放进 _read（bless 在事件循环里同步调读回调，
        阻塞或超时都会让 BlueZ 对 DEVICE_INFO 读回 0x0E Unlikely Error）。这里用 to_thread 把
        子进程移出事件循环，读时只取缓存 → 读回调瞬间返回。
        """
        while True:
            await self._refresh_network()
            await asyncio.sleep(NETWORK_REFRESH_SEC)

    async def _advertising_watchdog(self) -> None:
        """广告停滞(未广播且无连接)连续 [WATCHDOG_STALLED_TICKS] 跳时，仅重注册广播。

        「无连接」必须查 BlueZ Device1.Connected（见 _any_central_connected），不能用
        bless 的 server.is_connected()——其语义是「有特征值被订阅」，连接建立窗口内恒为
        False，而连上后 BlueZ 又停了广播，单看这两项会把每次握手都误判成停滞。
        历史教训（f7ded14）：恢复动作勿用 stop()+start()——那会注销重注册整个 GATT 应用、
        重分配全部 attribute handle，手机带着缓存表重连即撞 GATT_INVALID_HANDLE(status=1)；
        现改为只重注册 LEAdvertisement1（见 _restart_advertising_only），handle 永不漂移。
        """
        stalled_ticks = 0
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
            try:
                if self._server is None:
                    continue
                advertising = await self._server.is_advertising()
                connected = await self._any_central_connected()
                if connected is None:
                    # 连接状态查询失败：状态未知，本跳不动作也不累计
                    stalled_ticks = 0
                    continue
                stalled = (not advertising) and (not connected)
                if not stalled:
                    stalled_ticks = 0
                    continue
                stalled_ticks += 1
                if stalled_ticks < WATCHDOG_STALLED_TICKS:
                    logger.info(
                        "[watchdog] 广告停滞 %d/%d 跳 (connected=%s advertising=%s)",
                        stalled_ticks, WATCHDOG_STALLED_TICKS, connected, advertising,
                    )
                    continue
                stalled_ticks = 0
                logger.info(
                    "[watchdog] 广告停滞连续 %d 跳, 仅重注册广播 (connected=%s advertising=%s)",
                    WATCHDOG_STALLED_TICKS, connected, advertising,
                )
                await self._restart_advertising_only()
            except Exception as e:  # noqa: BLE001
                stalled_ticks = 0
                logger.warning("[watchdog] 异常: %s", e)

    async def _any_central_connected(self) -> Optional[bool]:
        """查 BlueZ 实际连接状态：任一 hci 下远端设备 Device1.Connected=true 即有连接。

        bless 0.3.0 的 server.is_connected() 返回 subscribed_characteristics 是否非空
        （v0.3.0 BlueZGattApplication.is_connected，源码注释自认 not the same as adapter
        connected）。手机连接建立窗口内（LL 已连上 → App 写 CCCD 订阅 STATUS 前，约
        0.3~2.5s）它恒为 False；旧看门狗据此误判「无连接+停广播」→ stop()+start() 杀掉
        建立中的连接或重分配句柄，正是 App 端「读取特征值失败 status=1」「连接在建立过程
        中断开」的根因。这里直接经 ObjectManager.GetManagedObjects 查真实连接。

        返回 None 表示查询失败（调用方跳过本跳）。
        """
        try:
            bus = await self._get_watchdog_bus()
            reply = await asyncio.wait_for(
                bus.call(
                    Message(
                        message_type=MessageType.METHOD_CALL,
                        destination="org.bluez",
                        path="/",
                        interface="org.freedesktop.DBus.ObjectManager",
                        member="GetManagedObjects",
                    )
                ),
                timeout=WATCHDOG_DBUS_TIMEOUT_SEC,
            )
        except Exception:
            self._reset_watchdog_bus()
            raise
        if reply.message_type != MessageType.METHOD_RETURN:
            return None
        for path, interfaces in reply.body[0].items():
            if "/dev_" not in str(path):
                continue  # 只要远端设备对象（/org/bluez/hciX/dev_XX_…）
            dev = interfaces.get("org.bluez.Device1") or {}
            connected = dev.get("Connected")
            if connected is not None and connected.value:
                return True
        return False

    async def _get_watchdog_bus(self) -> MessageBus:
        bus = self._watchdog_bus
        if bus is not None and bus.connected:
            return bus
        self._reset_watchdog_bus()
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._watchdog_bus = bus
        return bus

    def _reset_watchdog_bus(self) -> None:
        bus = self._watchdog_bus
        self._watchdog_bus = None
        if bus is not None and bus.connected:
            bus.disconnect()

    async def _restart_advertising_only(self) -> None:
        """只重注册 LE 广播（LEAdvertisement1），不注销 GATT 应用 → handle 不变。

        经 BlueZGattApplication.stop/start_advertising 只动 LEAdvertisingManager1 的
        广播注册；GATT 应用保持注册，attribute handle 不重分配，手机缓存的服务表持续有效。
        后端属性缺失（bless 变更/非 BlueZ 平台）时退回 stop()+start() 兜底恢复广播——
        有句柄漂移的代价，但好过永远不再广播。
        """
        server = self._server
        app = getattr(server, "app", None)
        adapter = getattr(server, "adapter", None)
        if app is None or adapter is None or not hasattr(app, "start_advertising"):
            logger.warning("[watchdog] 无 BlueZ 后端(app/adapter)，退回 stop()+start()")
            await server.stop()
            await server.start()
            return
        try:
            await app.stop_advertising(adapter)
        except Exception as e:  # noqa: BLE001
            logger.debug("[watchdog] stop_advertising 忽略: %s", e)
        await app.start_advertising(adapter)
