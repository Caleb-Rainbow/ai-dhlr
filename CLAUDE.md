# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DHLR (动火离人安全监测系统) is a fire safety monitoring system for unattended cooking areas. It uses computer vision (YOLO-based person detection) to monitor fire zones and triggers a three-stage alarm mechanism when no one is present.

## Build and Test Commands

### Backend (Python)
```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest

# Run specific test file
pytest tests/test_detector.py

# Run with coverage
pytest --cov=src tests/

# Start the server
python src/main.py
```

### Frontend (Vue 3 + TypeScript)
```bash
cd web/fire-monitor-ui

# Install dependencies
npm install

# Development server
npm run dev

# Production build
npm run build
```

## Architecture

### Backend Structure
```
src/
├── main.py              # Entry point, FireSafetySystem class
├── api/                 # FastAPI routes and WebSocket handlers
├── camera/              # Camera management (USB/RTSP)
├── detection/           # Person detection engine (YOLO)
├── zone/                # Zone state machine and management
├── output/              # GPIO control and voice playback
├── serial_port/         # Serial communication (MODBUS-RTU)
├── patrol/              # Patrol/inspection functionality
└── utils/               # Configuration, logging, performance
```

### Frontend Structure
```
web/fire-monitor-ui/src/
├── api/                 # WebSocket communication (ws.ts)
├── components/          # Vue components
├── composables/         # Vue composition functions
├── views/               # Page components (Dashboard, Zones, etc.)
└── types/               # TypeScript type definitions
```

### Key Components

- **FireSafetySystem** (`src/main.py`): Main class managing system lifecycle, detection loop, and alarm callbacks
- **ZoneStateMachine** (`src/zone/state_machine.py`): Three-stage alarm (WARNING → ALARM → CUTOFF) with timing thresholds
- **PersonDetector** (`src/detection/detector.py`): YOLO-based detection with frame smoothing, supports PyTorch and RKNN engines
- **CameraManager** (`src/camera/manager.py`): Multi-camera support (USB/RTSP)
- **SerialManager** (`src/serial_port/serial_manager.py`): MODBUS-RTU protocol for current detection and temperature sensors

### Three-Stage Alarm Mechanism
1. **WARNING**: No person for `warning_time` seconds (default 90s)
2. **ALARM**: No person for `alarm_time` seconds (default 180s)
3. **CUTOFF**: No person for `action_time` seconds (default 300s) - triggers power cutoff via serial

## Configuration

Configuration is loaded from `config/config.yaml`. Key sections:
- `cameras`: USB/RTSP camera definitions
- `zones`: Monitoring areas with ROI coordinates (normalized)
- `inference.engine`: `pytorch` for development, `rknn` for edge deployment
- `alarm`: Timing thresholds for warning/alarm/cutoff
- `serial`: Serial port settings for current detection

## WebSocket Protocol

The system uses WebSocket for real-time communication. See `docs/websocket_protocol.md` for full protocol specification. The frontend communicates via `web/fire-monitor-ui/src/api/ws.ts`.

## Testing

Tests use pytest with pytest-asyncio. Mock external dependencies (cameras, serial ports, GPIO) in tests. Configuration is in `pytest.ini` with `asyncio_mode = auto`.

## Dual Inference Engine

The detection engine supports two backends:
- **PyTorch**: For development and x86_64 deployment
- **RKNN**: For RK3568 edge devices (uses `rknn_toolkit_lite2`)

Engine selection is via `inference.engine` config. Factory pattern in `src/detection/engine/` creates the appropriate engine.
