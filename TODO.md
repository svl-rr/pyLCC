# pyLCC Project Roadmap & TODO List

A lightweight, modular OpenLCB / NMRA LCC (Layout Command Control) stack for MicroPython and Python.

---

## Progress Overview

- [x] **Step 1: Frame & Transport Abstraction**
- [x] **Step 2: CAN Alias Allocation Protocol**
- [x] **Step 3: Event Transport Protocol (Producer / Consumer Model)**
- [x] **Step 4: Datagram Transport & SNIP Engine**
- [x] **Step 5: Memory Configuration Protocol & CDI XML**
- [x] **Step 6: High-Level Node Abstraction & Application API**
- [x] **Step 7: Hardware CAN Drivers (STM32, ESP32-C6, Pico MCP2517FD)**
- [x] **Step 8: Peripheral I/O, JMRI Integration & Packaging**

---

## Detailed Task Breakdown

### Completed Work

- [x] **CAN Frame Model (`pylcc.can.CanFrame`)**
  - 29-bit extended identifier bitfield extraction (`source_alias`, `variable_field`, `mti`, `is_addressed`, `destination_alias`).
  - Constructors for CAN Control frames (`CID`, `RID`, `AMD`, `AME`, `AMR`).
  - Constructors for OpenLCB Message frames (Global & Addressed).
  - First-class Datagram frames (Formats 2, 3, 4, 5: `Only`, `First`, `Middle`, `Final`) and ACK/NAK (`Datagram OK`, `Datagram Rejected`).
- [x] **GridConnect Protocol (`pylcc.gridconnect`)**
  - Frame encoder (`:X195B422AN...;`).
  - Strict validator & decoder.
  - Streaming parser (`GridConnectParser`) handling partial chunks, corrupted data, and multiple frames per read.
- [x] **Transport Layer (`pylcc.transport`)**
  - `BaseTransport`: Common async send/receive interface with listener hooks.
  - `LoopbackTransport` & `LoopbackBus`: Fast in-memory bus for offline automated testing.
  - `GridConnectTcpClient`: Asynchronous TCP client for WiFi/Ethernet connections to JMRI or LCC Hubs.
  - `GridConnectTcpServer`: Standalone TCP hub for multi-client connections.
  - `GridConnectSerialTransport`: USB CDC / UART serial transport supporting MicroPython (`machine.UART`) and desktop Python (`pyserial`).
- [x] **Node ID & PRNG (`pylcc.node_id`, `pylcc.alias`)**
  - 48-bit `NodeID` model with 12-bit CID chunking (`chunk_7` through `chunk_4`).
  - Heidi G. Kuehn (1961) 48-bit Linear Congruential PRNG specified in OpenLCB **TN-9.7.2.1**.
  - `AliasManager` state machine (`INHIBITED` $\to$ `CHECKING` $\to$ `PERMITTED`).
  - 100 ms conflict observation timer and back-off retry on collision.
  - Active defense transmitting `RID` against newcomers probing with `CID`.
  - Handling of `AME` queries and release with `AMR`.
- [x] **Event Transport (`pylcc.event_id`, `pylcc.events`)**
  - 64-bit `EventID` model with `EventID.from_node_id(node_id, index)` and `encode_range()` / `decode_range()`.
  - `EventManager` for registering local consumers (with sync/async callbacks) and producers.
  - Power-of-two Consumer & Producer Event Ranges with **Consumer/Producer Range Identified** (`0x04A4` / `0x0524`) announcements on startup and queries.
  - Broadcasting Producer/Consumer Event Reports (`PCER` - `0x05B4`).
  - Automatic query responses for:
    - Identify Consumer (`0x08F4`) $\to$ `Consumer Identified` (`0x04C4`/`0x04C5`/`0x04C7`).
    - Identify Producer (`0x0914`) $\to$ `Producer Identified` (`0x0544`/`0x0545`/`0x0547`).
    - Identify Events Global (`0x0976`) & Addressed (`0x0968`) $\to$ full event & range enumeration.
- [x] **Datagram Transport (`pylcc.datagram`) (NMRA Standard S-9.7.3.2 / S-9.7.2.1)**
  - Multi-frame fragmentation and reassembly up to 72 bytes across 8-byte CAN frames.
  - Flow control with automatic `Datagram Received OK` (`0x0A28`) and `Datagram Rejected` (`0x0A48`) NAK with error codes.
  - Dispatching datagrams by protocol byte to registered handlers.
  - Asynchronous `send_datagram()` with timeout and ACK confirmation.
- [x] **Simple Node Information & Protocol Support (`pylcc.snip`) (S-9.7.4.3 / S-9.7.3)**
  - `SnipData` model and encoder/decoder (Manufacturer, Model, Hardware/Software versions, User Name & Description).
  - `SnipService`: Streams SNIP reply frames (`0x0A08`) in response to `0x0DE8`.
  - Protocol Support Inquiry (`PIP` - `0x0828`) handler replying with capability bitmask (`0x0668`).
- [x] **Memory Configuration Protocol, ACDI & CDI XML (`pylcc.memory`, `pylcc.cdi`) (S-9.7.4.2 / S-9.7.4.1 / S-9.7.4.3)**
  - `MemorySpace`, `BytesMemorySpace`, `ConfigMemorySpace`, `AcdiManufacturerSpace` (`0xFC`), and `AcdiUserSpace` (`0xFB`) storage backends synced live with `SnipData`.
  - Configuration Datagram (0x20) handler (`MemoryConfigurationService`):
    - Memory Read (`0x40` generic, `0x41` CDI, `0x42` All-Memory, `0x43` Config) $\to$ Read Replies (`0x50`, `0x51`, `0x52`, `0x53`) and Failure (`0x58`).
    - Memory Write (`0x00` generic, `0x03` Config) $\to$ Write Replies (`0x10`, `0x13`) and Failure (`0x18`).
    - Get Configuration Options (`0x80`) $\to$ Options Reply (`0x82`).
    - Get Space Info (`0x84`) $\to$ Space Info Reply (`0x86`).
    - Node Restart / Reset Command (`0xA8`) with async callback trigger.
  - Pure MicroPython Programmatic CDI XML Generator (`CdiDocument`, `CdiSegment`, `CdiGroup`, `CdiInt`, `CdiString`, `CdiEventID`):
    - Generates valid XML conforming to schema `http://openlcb.org/schema/cdi/1/1/cdi.xsd`.
    - Auto-calculates memory offsets for elements across segments and replicated groups.
    - Generates initial default binary memory images and instantiates pre-populated `ConfigMemorySpace` with flash file persistence.
    - Exports null-terminated UTF-8 byte stream required by JMRI.
- [x] **Core Node Abstraction & Layout Component Stubs (`pylcc.node`, `pylcc.components`)**
  - Unified `Node` class orchestrating `Transport`, `AliasManager`, `EventManager`, `DatagramManager`, `SnipService`, and `MemoryConfigurationService`.
  - Message Network Discovery & Error Handling (NMRA S-9.7.3): responds to `Verify Node ID` Global (`0x0490`) & Addressed (`0x0488`) with `Verified Node ID` (`0x0170`), and emits `Optional Interaction Rejected` (`0x0068`) for unsupported addressed MTIs.
  - Single-task sequential frame dispatch loop cutting MicroPython Task heap allocations by 75%.
  - Live CDI-to-Component Binding: auto-generates CDI schema, applies persisted flash EventIDs/settings on startup, and live-rebinds component EventIDs & SNIP user name on Space `0xFD` writes without rebooting.
  - Lifecycle management: alias allocation, Initialization Complete (`0x0100`) broadcast, and AMR release on shutdown.
  - Clean reference component stubs with callback hooks for custom hardware drivers:
    - `Turnout`: Consumes Close/Throw events, produces Is Closed/Is Thrown feedback, `on_change` hook.
    - `Sensor` / `BlockDetector` / `Input`: Produces Active/Inactive events, responds to Producer Identify inquiries.
    - `Signal` / `SignalMast`: Consumes arbitrary aspect events (STOP, APPROACH, CLEAR, etc.), `on_aspect_change` hook.
    - `Output` / `Light`: Consumes On/Off discrete command events.
- [x] **Modular Hardware CAN Drivers (`pylcc.hardware`)**
  - Modular, isolated architecture saving flash and RAM on target microcontrollers (zero cross-platform imports).
  - **ESP32 / ESP32-C6 TWAI Driver (`pylcc.hardware.esp32_twai`)**:
    - Direct interface to on-chip Two-Wire Automotive Interface (Classical CAN 2.0B) via `machine.TWAI` / `machine.CAN`.
    - Configured for standard OpenLCB 125 kbps with 29-bit extended frames and non-blocking RX pump.
  - **Raspberry Pi Pico / Pico 2 MCP2517FD / MCP2518FD Driver (`pylcc.hardware.mcp2518fd`)**:
    - High-speed SPI controller driver for RP2040 / RP2350 (up to 20 MHz).
    - Deep 31-message hardware FIFO configuration (`FSIZE=30`) eliminating packet loss during 125 kbps bus bursts.
  - **STM32 Native CAN Driver (`pylcc.hardware.stm32_can`)**:
    - Direct interface to STM32 on-chip CAN controller via MicroPython's `pyb.CAN`.
    - Extended 32-bit filter mask accepting all 29-bit OpenLCB frames into FIFO 0.
- [x] **Bidirectional CAN <-> WiFi Gateway Bridge (`pylcc.bridge.LCCBridge`)**
  - Bidirectional frame bridging between any two `BaseTransport` instances (e.g. native CAN $\leftrightarrow$ GridConnect TCP).
  - Transparent message forwarding, filtering loopback bounce, and real-time packet counter statistics.
- [x] **Packaging, Distribution & Documentation (Step 8)**
  - `pyproject.toml` (PEP 621) for desktop Python and pip installation.
  - Modular MicroPython `mip` manifests (`package.json`, `package-esp32.json`, `package-pico.json`, `package-stm32.json`, `package-all.json`).
  - Hardware wiring documentation (`docs/hardware_wiring.md`) with pinouts and connection diagrams for ESP32-C6, Raspberry Pi Pico RP2040/RP2350 + MCP2518FD, and STM32.
  - Step-by-step JMRI integration guide (`docs/jmri_integration.md`) covering discovery, CDI configuration via DecoderPro, turnout/sensor tables, and wireless gateway connection.
  - MIT License (`LICENSE`).
- [x] **Validation & Tooling**
  - 95 automated unit tests (100% passing).
  - Comprehensive runnable examples (`mock_lcc_hub`, `gridconnect_tcp_monitor`, `virtual_turnout_node`, `jmri_interactive_node`, `micropython_wifi_node`, `wifi_can_bridge`).
- [x] **Optimizations & MicroPython Tuning (Performance & Memory)**
  - [x] **Zero-Allocation Event Filtering (`pylcc/events.py`)**: Fast 8-byte dict lookups (`_consumers_by_bytes` / `_producers_by_bytes`) and synchronous `has_interest()` filtering without instantiating `EventID` objects.
  - [x] **C-Speed Hex Conversion (`pylcc/gridconnect.py`)**: Uses `binascii.hexlify` / `unhexlify` (`ubinascii` on MicroPython) instead of Python character loops.
  - [x] **Byte-Level Delimiter Scanning (`pylcc/gridconnect.py`)**: Pre-allocated `bytearray` buffer with C-speed `.find(b":")` / `.find(b";")` in `GridConnectParser`.
  - [x] **Bitwise Header Unpacking Without Slices (`pylcc/can.py`, `pylcc/datagram.py`)**: Direct integer bit arithmetic in `destination_alias`, `make_addressed_message`, and NAK error unpacking.
  - [x] **Cached Immutable Byte Serializations (`pylcc/node_id.py`, `pylcc/event_id.py`)**: Cached `_bytes` in `__slots__` so `to_bytes()` returns the existing reference.
  - [x] **Stale Datagram Buffer Eviction & Zero-List Fragmentation (`pylcc/datagram.py`)**: Monotonic millisecond timeout eviction (3s default) and direct offset loop fragmentation.
  - [x] **Bounded / Listener-Aware RX Queue (`pylcc/transport/base.py`)**: Eliminates unbounded `_rx_queue` memory leak when listeners are attached and caps unread queue capacity at 32 frames.
  - [x] **Synchronous Fast-Path Routing (`pylcc/node.py`)**: Filters irrelevant frames synchronously before allocating `asyncio` tasks.
  - [x] **Hardware Driver Optimization (`pylcc/hardware/mcp2518fd.py`, `esp32_twai.py`, `stm32_can.py`)**: Module-level sleep helpers, 8-frame burst FIFO draining, active-low `int_pin` check, and cached `TXQCON`/`FIFOCON1` writes.
  - [x] **Zero-Copy Read-Only Spaces & `__slots__`/`const()` (`pylcc/memory.py`, `alias.py`, `snip.py`, `bridge.py`, `components/`)**: Avoids copying read-only CDI `bytes` and minimizes MicroPython RAM/bytecode overhead.

---

## Remaining Work

None — all planned protocol features, hardware drivers, packaging, docs, and performance optimizations are complete!
