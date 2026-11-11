# pyLCC Project Roadmap & TODO List

A lightweight, modular OpenLCB / NMRA LCC (Layout Command Control) stack for MicroPython and Python.

---

## Progress Overview

- [x] **Step 1: Frame & Transport Abstraction**
- [x] **Step 2: CAN Alias Allocation Protocol**
- [x] **Step 3: Event Transport Protocol (Producer / Consumer Model)**
- [x] **Step 4: Datagram Transport & SNIP Engine**
- [x] **Step 5: Memory Configuration Protocol & CDI XML**
- [ ] **Step 6: High-Level Node Abstraction & Application API**
- [ ] **Step 7: Hardware CAN Drivers (STM32, ESP32-C6, Pico MCP2517FD)**
- [ ] **Step 8: Peripheral I/O, JMRI Integration & Packaging**

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
- [x] **Node ID & PRNG (`pylcc.node_id`, `pylcc.alias`)**
  - 48-bit `NodeID` model with 12-bit CID chunking (`chunk_7` through `chunk_4`).
  - Heidi G. Kuehn (1961) 48-bit Linear Congruential PRNG specified in OpenLCB **TN-9.7.2.1**.
  - `AliasManager` state machine (`INHIBITED` $\to$ `CHECKING` $\to$ `PERMITTED`).
  - 100 ms conflict observation timer and back-off retry on collision.
  - Active defense transmitting `RID` against newcomers probing with `CID`.
  - Handling of `AME` queries and release with `AMR`.
- [x] **Event Transport (`pylcc.event_id`, `pylcc.events`)**
  - 64-bit `EventID` model with `EventID.from_node_id(node_id, index)`.
  - `EventManager` for registering local consumers (with sync/async callbacks) and producers.
  - Broadcasting Producer/Consumer Event Reports (`PCER` - `0x05B4`).
  - Automatic query responses for:
    - Identify Consumer (`0x08F4`) $\to$ `Consumer Identified` (`0x04C4`/`0x04C5`/`0x04C7`).
    - Identify Producer (`0x0914`) $\to$ `Producer Identified` (`0x0544`/`0x0545`/`0x0547`).
    - Identify Events Global (`0x0976`) & Addressed (`0x0968`) $\to$ full event enumeration.
- [x] **Datagram Transport (`pylcc.datagram`) (NMRA Standard S-9.7.3.2 / S-9.7.2.1)**
  - Multi-frame fragmentation and reassembly up to 72 bytes across 8-byte CAN frames.
  - Flow control with automatic `Datagram Received OK` (`0x0A28`) and `Datagram Rejected` (`0x0A48`) NAK with error codes.
  - Dispatching datagrams by protocol byte to registered handlers.
  - Asynchronous `send_datagram()` with timeout and ACK confirmation.
- [x] **Simple Node Information & Protocol Support (`pylcc.snip`) (S-9.7.4.3 / S-9.7.3)**
  - `SnipData` model and encoder/decoder (Manufacturer, Model, Hardware/Software versions, User Name & Description).
  - `SnipService`: Streams SNIP reply frames (`0x0A08`) in response to `0x0DE8`.
  - Protocol Support Inquiry (`PIP` - `0x0828`) handler replying with capability bitmask (`0x0668`).
- [x] **Memory Configuration Protocol & CDI XML (`pylcc.memory`, `pylcc.cdi`) (S-9.7.4.2 / S-9.7.4.1)**
  - `MemorySpace`, `BytesMemorySpace`, and `ConfigMemorySpace` storage backends.
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
- [x] **Validation & Tooling**
  - 50 automated unit tests (100% passing).
  - MicroPython bytecode compilation verified via `mpy-cross` across all modules.

---

### Remaining Work

### Optimizations & MicroPython Tuning (Performance & Memory)
- [ ] **Zero-Allocation Event Filtering (`pylcc/events.py`)**:
  - Filter incoming PCER event reports using fast 64-bit integer set lookups before instantiating `EventID` objects.
  - Prevents heap allocations for unconsumed layout events on busy buses.
- [ ] **C-Speed Hex Conversion (`pylcc/gridconnect.py`)**:
  - Utilize built-in `ubinascii.hexlify` and `unhexlify` (or `bytes.fromhex`) to bypass Python character loops and generator overhead during frame encoding/decoding.
- [ ] **Byte-Level Delimiter Scanning in `GridConnectParser` (`pylcc/gridconnect.py`)**:
  - Replace per-character string decoding and list appending with a pre-allocated `bytearray` buffer and C-speed delimiter search (`find(b":")` / `find(b";")`).
- [ ] **Bitwise Header Unpacking Without Slices (`pylcc/can.py`)**:
  - Replace `self.data[:2]` byte slicing in `destination_alias` with direct integer bit arithmetic (`((self.data[0] << 8) | self.data[1]) & 0x0FFF`) to eliminate temporary slice allocations.
- [ ] **Cached Immutable Byte Serializations (`pylcc/node_id.py`, `pylcc/event_id.py`)**:
  - Pre-compute and cache `_bytes` in `__init__` so `to_bytes()` returns the cached reference without heap re-allocation.
- [ ] **Stale Datagram Buffer Eviction (`pylcc/datagram.py`)**:
  - Implement timestamp tracking and a 3-second eviction timeout for partial multi-frame datagram reassembly buffers (per NMRA S-9.7.3.2 Section 4.2) to prevent memory leaks from dropped transmissions.

---

### Step 6: Core Node Abstraction & High-Level API
- [ ] **Unified `Node` Class (`pylcc.node`)**
  - [ ] Encapsulate transport, alias manager, event manager, datagram manager, SNIP, and memory config into a single cohesive class:
    ```python
    node = Node(node_id="05.01.01.01.14.01", transport=transport)
    await node.start()
    ```
- [ ] **High-Level Layout Component Helpers (`pylcc.components`)**
  - [ ] `Turnout`: Handles Thrown / Closed event consumption and feedback event production.
  - [ ] `BlockDetector`: Debounced block occupancy sensor triggering Occupied / Cleared events.
  - [ ] `PushButton`: Momentary push button producing configured events.
  - [ ] `SignalMast`: Multi-aspect signal mast consuming aspect events.

---

### Step 7: Hardware CAN Drivers (Target Platforms)
- [ ] **STM32 Native CAN Driver (`pylcc.hardware.stm32_can`)**
  - [ ] Direct interface to STM32 on-chip CAN / FDCAN peripheral via MicroPython's `pyb.CAN` / `machine.CAN`.
  - [ ] Configure 125 kbps bitrate, 29-bit extended filters, and non-blocking RX/TX loops.
- [ ] **ESP32-C6 Native TWAI Driver (`pylcc.hardware.esp32_twai`)**
  - [ ] ESP32-C6 built-in TWAI (Classical CAN 2.0B) driver interface with external 3.3V transceiver.
  - [ ] Pin configuration (TX/RX GPIO) and interrupt/async polling.
- [ ] **Raspberry Pi Pico (RP2040 / RP2350) MCP2517FD / MCP2518FD Driver (`pylcc.hardware.mcp2518fd`)**
  - [ ] High-speed SPI driver (up to 20 MHz) for the MCP2517FD / MCP2518FD external controller.
  - [ ] 31-message deep FIFO configuration to ensure zero dropped packets during 125 kbps bus bursts.

---

### Step 8: Integration, Testing & Packaging
- [ ] **JMRI End-to-End Validation**
  - [ ] Test node discovery in JMRI OpenLCB Network Tree.
  - [ ] Configure node parameters using JMRI DecoderPro CDI Tool.
  - [ ] Test Turnout and Sensor table integration with live event updates.
- [ ] **Hardware Wiring Diagrams & Documentation**
  - [ ] ESP32-C6 + SN65HVD230 transceiver pinout.
  - [ ] Raspberry Pi Pico + MCP2518FD SPI breakout pinout.
  - [ ] STM32 CAN transceiver connection guide.
- [ ] **Distribution & Packaging**
  - [ ] `pyproject.toml` for standard `pip` installation on desktop Python.
  - [ ] `package.json` manifest for MicroPython `mip` installation directly from GitHub.
