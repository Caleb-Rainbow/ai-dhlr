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

from . import protocol as P
from .network_applier import current_network_status

logger = logging.getLogger(__name__)


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

    def bind_service(self, service) -> None:
        self._service = service

    # ---------------------------- bless 回调 ---------------------------- #
    def _read(self, characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
        if str(characteristic.uuid) == P.DEVICE_INFO_UUID:
            # 每次读取动态拼入当前网络状态（IP/上行类型会随配网变化）
            return bytearray(P.build_device_info(**self._identity, network=current_network_status()))
        return bytearray(b"")

    def _write(self, characteristic: BlessGATTCharacteristic, value: bytearray, **kwargs) -> None:
        if str(characteristic.uuid) != P.REQUEST_UUID:
            return
        logger.info(f"[write] 收到 {len(value)} 字节写入 REQUEST")
        for seq, obj in self._decoder.feed(bytes(value)):
            logger.info(f"[write] 解析出帧 seq={seq} obj_keys={list(obj.keys())} cmd={obj.get('cmd')}")
            if self._service is not None:
                try:
                    asyncio.create_task(self._service.handle(seq, obj))
                except RuntimeError as e:
                    logger.error(f"[write] 派发失败(无事件循环?): {e}")

    # ---------------------------- 状态通知 ---------------------------- #
    async def notify(self, obj: dict) -> None:
        """分帧并通过 STATUS 特征值下发通知（best-effort）。

        bless 的 update_value(service_uuid, char_uuid) 是「读特征值当前值再通知」，
        故需先把每块 chunk 写入特征值 .value，再 update_value 触发 PropertiesChanged。
        """
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
            for chunk in chunks:
                char.value = bytearray(chunk)
                self._server.update_value(P.SERVICE_UUID, P.STATUS_UUID)
                await asyncio.sleep(0)
        except Exception as e:  # noqa: BLE001
            logger.warning("notify 失败: %s", e)

    # ---------------------------- 生命周期 ---------------------------- #
    async def start(self) -> None:
        server = BlessServer(name=self._name)
        self._server = server
        server.read_request_func = self._read
        server.write_request_func = self._write

        await server.add_new_service(P.SERVICE_UUID)

        await server.add_new_characteristic(
            P.SERVICE_UUID, P.DEVICE_INFO_UUID,
            GATTCharacteristicProperties.read,
            bytearray(P.build_device_info(**self._identity, network=current_network_status())),
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
        # 看门狗：bless 在中央断开后不会自动恢复广播，需主动重广播
        asyncio.create_task(self._advertising_watchdog())

    async def _advertising_watchdog(self) -> None:
        """bless 断开后不自动恢复广播；检测到断开或广播停时 stop()+start() 重广播。"""
        was_connected = False
        while True:
            await asyncio.sleep(5)
            try:
                if self._server is None:
                    continue
                connected = await self._server.is_connected()
                advertising = await self._server.is_advertising()
                just_disconnected = was_connected and not connected
                stalled = (not advertising) and (not connected)
                if just_disconnected or stalled:
                    logger.info(
                        "[watchdog] 重新广播 (connected=%s advertising=%s)",
                        connected, advertising,
                    )
                    try:
                        await self._server.stop()
                    except Exception as e:  # noqa: BLE001
                        logger.debug("[watchdog] stop 忽略: %s", e)
                    await self._server.start()
                was_connected = connected
            except Exception as e:  # noqa: BLE001
                logger.warning("[watchdog] 异常: %s", e)
