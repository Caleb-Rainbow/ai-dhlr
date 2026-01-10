# 单元测试文档

本文档介绍了 `tests/` 目录下的所有单元测试用例，涵盖了项目核心模块的测试。

## 目录结构

```
tests/
├── __init__.py           # 测试包初始化文件
├── conftest.py           # 测试配置和共享 fixtures
├── test_config.py        # 配置模块测试
├── test_models.py        # 数据模型测试
├── test_serial_helper.py # 串口通信助手测试
└── test_state_machine.py # 状态机测试
```

---

## conftest.py - 测试配置和共享 Fixtures

此文件定义了测试所需的共享配置和 fixtures，为其他测试文件提供统一的 mock 对象。

### Mock 配置数据类

| 类名 | 描述 |
|------|------|
| `MockAlarmConfig` | 模拟报警配置，包含预警时间、报警时间、切电时间等参数 |
| `MockAppConfig` | 模拟应用配置，包含 `MockAlarmConfig` 实例 |

### Fixtures

| Fixture 名称 | 描述 |
|--------------|------|
| `mock_config` | 提供测试用配置对象 |
| `mock_logger` | 提供 mock 日志记录器，包含 `info`、`debug`、`warning`、`error` 方法 |
| `mock_event_logger` | 提供 mock 事件记录器，包含事件记录和快照保存功能 |
| `sample_zone_config` | 提供测试用灶台配置 |
| `patched_dependencies` | 统一 patch 所有外部依赖 |

---

## test_config.py - 配置模块测试

测试 `src/utils/config.py` 模块中各个配置数据类的默认值和基本功能。

### TestCameraConfig - 摄像头配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证摄像头配置的默认值（分辨率 640x480，帧率 30fps） |
| `test_usb_camera` | 测试 USB 摄像头配置，验证设备索引设置 |
| `test_rtsp_camera` | 测试 RTSP 网络摄像头配置，验证 RTSP URL 设置 |

### TestZoneConfig - 灶台配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证灶台配置默认值（已启用、串口索引 0、火焰电流阈值 100） |
| `test_custom_values` | 测试自定义配置值的正确应用 |

### TestAlarmConfig - 报警配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证三阶段报警的默认时间（预警 90s、报警 180s、切电 300s） |
| `test_custom_times` | 测试自定义报警时间配置 |

### TestInferenceConfig - 推理配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证推理配置默认值（引擎 pytorch、模型 yolo11n.pt、置信度阈值 0.5） |

### TestDetectionConfig - 检测配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证检测配置默认阈值（无人阈值 3、有人阈值 2） |

### TestApiConfig - API 配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证 API 配置默认值（主机 0.0.0.0、端口 8000、CORS 允许所有来源） |

### TestSerialConfig - 串口配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证串口配置默认值（端口 /dev/ttyS3、波特率 9600、轮询间隔 1.0s） |

### TestSystemConfig - 系统配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证系统配置默认值（系统名称包含"监测"或"动火"、调试模式开启） |

### TestTTSConfig - TTS 配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证 TTS 配置默认值（引擎 kokoro、音频目录 audio_assets、空闲超时 60s） |

### TestRemoteServerConfig - 远程服务器配置测试

| 测试方法 | 描述 |
|----------|------|
| `test_default_values` | 验证远程服务器配置默认值（默认禁用、WebSocket 路径 dhlr/socket） |

---

## test_models.py - 数据模型测试

测试 `src/zone/models.py` 模块中的灶台状态枚举和数据类。

### TestZoneState - 灶台状态枚举测试

| 测试方法 | 描述 |
|----------|------|
| `test_all_states_exist` | 验证所有期望的状态值存在（IDLE、ACTIVE_WITH_PERSON、ACTIVE_NO_PERSON、WARNING、ALARM、CUTOFF） |
| `test_state_count` | 验证状态枚举包含正确数量（6个）的状态 |

### TestZone - Zone 数据类测试

| 测试方法 | 描述 |
|----------|------|
| `test_zone_creation` | 测试 Zone 实例的正确创建 |
| `test_zone_default_values` | 验证 Zone 的默认值（启用状态、IDLE 状态、无火、无人等） |
| `test_to_dict` | 测试 `to_dict()` 序列化方法的正确性 |
| `test_to_dict_with_countdown` | 测试带有倒计时数据的 `to_dict()` 序列化 |

### TestZoneStatusText - Zone 状态文本测试

| 测试方法 | 描述 |
|----------|------|
| `test_status_text_mapping` | 使用参数化测试验证各状态的中文文本映射（空闲、有人看管、无人看管、预警中、报警中、已切电） |

---

## test_serial_helper.py - 串口通信助手测试

测试 `src/serial_port/serial_helper.py` 模块中的 CRC 计算、命令构建等功能。

### TestCRC16 - CRC16-Modbus 计算测试

| 测试方法 | 描述 |
|----------|------|
| `test_known_crc_value` | 使用已知 Modbus 命令验证 CRC 计算正确性 |
| `test_another_known_crc` | 另一个已知 CRC 验证测试（写单个线圈命令） |
| `test_empty_data` | 测试空数据的 CRC（应为初始值 0xFFFF） |
| `test_single_byte` | 测试单字节数据的 CRC 计算 |
| `test_start_address` | 测试 `start_address` 参数的偏移量计算功能 |

### TestAppendCRC16 - CRC16 追加测试

| 测试方法 | 描述 |
|----------|------|
| `test_append_crc` | 测试 CRC 正确追加到数据末尾 |
| `test_append_crc_empty` | 测试空数据追加 CRC 的行为 |

### TestCommandBuilder - 命令构建测试

| 测试方法 | 描述 |
|----------|------|
| `test_build_get_current_command_index_0` | 测试构建获取电流命令（索引 0），验证地址、功能码和数据 |
| `test_build_get_current_command_index_1` | 测试构建获取电流命令（索引 1），验证地址变化 |
| `test_build_set_relay_command` | 测试构建设置继电器命令 |
| `test_build_get_lora_id_command` | 测试构建获取 LoRa ID 命令 |
| `test_build_set_lora_id_command` | 测试构建设置 LoRa ID 命令 |
| `test_build_set_lora_channel_command` | 测试构建设置 LoRa 信道命令 |
| `test_lora_id_overflow` | 测试 LoRa ID 溢出处理（值 > 0xFF 时的截断行为） |

### TestCRCVerification - CRC 验证测试

| 测试方法 | 描述 |
|----------|------|
| `test_valid_crc` | 测试有效 CRC 验证通过 |
| `test_invalid_crc` | 测试无效 CRC 验证失败 |
| `test_too_short_data` | 测试过短数据（< 2 字节）验证失败 |

---

## test_state_machine.py - 状态机测试

测试 `src/zone/state_machine.py` 模块中的灶台状态机逻辑。

### TestZoneStateMachine - 灶台状态机基础测试

| 测试方法 | 描述 |
|----------|------|
| `test_initial_state_is_idle` | 验证状态机初始状态为 IDLE |
| `test_fire_off_stays_idle` | 测试未开火时保持空闲状态（无论有无人） |
| `test_fire_on_with_person` | 测试开火+有人 → ACTIVE_WITH_PERSON 状态转换 |
| `test_fire_on_no_person_starts_countdown` | 测试开火+无人开始计时，进入 ACTIVE_NO_PERSON 状态 |
| `test_person_returns_resets_state` | 测试人员回场重置状态到 ACTIVE_WITH_PERSON |

### TestStateTransitions - 状态转换测试

| 测试方法 | 描述 |
|----------|------|
| `test_warning_callback` | 测试预警回调触发（无人时间超过 warning_time） |
| `test_alarm_callback` | 测试报警回调触发（无人时间超过 alarm_time） |
| `test_cutoff_callback` | 测试切电回调触发（无人时间超过 action_time） |

### TestManualReset - 手动复位测试

| 测试方法 | 描述 |
|----------|------|
| `test_reset_from_warning` | 测试从预警状态复位成功 |
| `test_reset_from_cutoff` | 测试从切电状态复位成功 |
| `test_reset_from_idle_fails` | 测试从空闲状态复位失败（返回 False） |
| `test_reset_from_active_fails` | 测试从有人动火状态复位失败（返回 False） |

### TestStateChangeEvent - 状态变化事件测试

| 测试方法 | 描述 |
|----------|------|
| `test_state_change_event_fired` | 测试状态变化时事件回调被正确触发 |
| `test_no_event_when_same_state` | 测试相同状态不触发重复事件 |

---

## 运行测试

### 安装依赖

```bash
pip install pytest pytest-asyncio
```

### 运行所有测试

```bash
pytest tests/ -v
```

### 运行特定测试文件

```bash
pytest tests/test_models.py -v
pytest tests/test_config.py -v
pytest tests/test_serial_helper.py -v
pytest tests/test_state_machine.py -v
```

### 运行特定测试类

```bash
pytest tests/test_models.py::TestZoneState -v
pytest tests/test_state_machine.py::TestManualReset -v
```

### 生成测试覆盖率报告

```bash
pip install pytest-cov
pytest tests/ --cov=src --cov-report=html
```

---

## 测试统计

| 测试文件 | 测试类数量 | 测试方法数量 |
|----------|------------|--------------|
| `test_config.py` | 11 | 16 |
| `test_models.py` | 3 | 8 |
| `test_serial_helper.py` | 4 | 14 |
| `test_state_machine.py` | 4 | 13 |
| **总计** | **22** | **51** |

---

## 测试设计原则

1. **隔离性**：每个测试用例独立运行，不依赖其他测试的执行顺序
2. **Mock 外部依赖**：使用 `unittest.mock` 模拟配置、日志、事件记录等外部依赖
3. **参数化测试**：使用 `@pytest.mark.parametrize` 减少重复代码
4. **Fixtures 共享**：通过 `conftest.py` 共享常用的测试配置和 mock 对象
5. **边界条件测试**：包含空数据、溢出、无效输入等边界条件测试
