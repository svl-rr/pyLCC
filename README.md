# pyLCC

A lightweight, modular **OpenLCB / NMRA LCC (Layout Command Control)** protocol stack designed to run on **MicroPython** microcontrollers as well as desktop Python.

---

## Target Platforms & Hardware Interfaces

1. **ESP32-C6**:
   - **Native Hardware CAN**: Built-in TWAI (Two-Wire Automotive Interface / CAN 2.0B) controller + external 3.3V transceiver (e.g. SN65HVD230).
   - **Network**: WiFi GridConnect over TCP.
2. **Raspberry Pi Pico / Pico 2 (RP2040 / RP2350)**:
   - **Hardware CAN**: **MCP2517FD / MCP2518FD** over high-speed SPI (with deep 31-message FIFO to eliminate buffer overflow under 125 kbps LCC bursts; legacy MCP2515 is not supported due to shallow 2-buffer limitations).
   - **Network**: WiFi GridConnect over TCP (Pico W).
3. **STM32**:
   - **Native Hardware CAN**: Built-in on-chip CAN / FDCAN controller using MicroPython's native interface (`pyb.CAN` / `machine.CAN`).
4. **Desktop Python (macOS / Linux / Windows)**:
   - Run virtual nodes, hubs, test monitors, and automated test suites directly against JMRI or physical adapters.

---

## Quickstart: Creating an OpenLCB Node

With `pyLCC`, creating a full-featured OpenLCB node with turnout control, block detection, and automatic JMRI CDI configuration takes only a few lines of code:

```python
import asyncio
from pylcc import LCCNode, LCCTurnout, LCCSensor
from pylcc.transport import GridConnectTcpClient

async def main():
    # 1. Connect to JMRI or an LCC WiFi hub via GridConnect TCP
    transport = GridConnectTcpClient("192.168.1.100", 12021)

    # 2. Instantiate node orchestrator (supports LCCNode or Node)
    node = LCCNode(node_id="05.01.01.01.14.01", transport=transport)

    # 3. Attach layout components (LCCTurnout/Turnout, LCCSensor/Sensor)
    turnout = LCCTurnout(
        name="Mainline Turnout",
        close_event="05.01.01.01.14.01.00.01",
        throw_event="05.01.01.01.14.01.00.02",
        on_change=lambda state: print(f"Turnout moved: {state}")
    )
    sensor = LCCSensor(
        name="Block 1 Detector",
        active_event="05.01.01.01.14.01.00.10",
        inactive_event="05.01.01.01.14.01.00.11",
        on_change=lambda state: print(f"Sensor changed: {state}")
    )
    node.add_component(turnout)
    node.add_component(sensor)

    # 4. Start node (allocates CAN alias, broadcasts Init Complete, generates CDI)
    await node.start()

    # Trigger events from physical hardware interrupts or polling
    await sensor.set_active(True)  # Broadcasts PCER to LCC network!

asyncio.run(main())
```

---

## Protocol Stack & Architectural Layers

### Step 1: Frame & Transport Abstraction (`pylcc.can`, `pylcc.gridconnect`, `pylcc.transport`)
- **CAN Frame Model (`CanFrame`)**: 29-bit extended identifier bitfield extraction compliant with **NMRA S-9.7.2.1**.
- **Control Frames**: First-class support for `CID 7..4`, `RID`, `AMD`, `AME`, and `AMR`.
- **GridConnect Protocol**: ASCII serialization (`:X195B422AN...;`) and streaming parser resilient to packet fragmentation and garbage recovery.
- **Transports**: `BaseTransport`, `LoopbackTransport` & `LoopbackBus` (for in-memory testing), `GridConnectTcpClient`, and `GridConnectTcpServer`.

### Step 2: CAN Alias Allocation Protocol (`pylcc.node_id`, `pylcc.alias`)
- **Node ID Model (`NodeID`)**: 48-bit unique identifier representation (`05.01.01.01.14.00`) with 12-bit CID chunk extraction.
- **48-Bit PRNG (`AliasPrng`)**: Implements Heidi G. Kuehn (1961) algorithm specified in **TN-9.7.2.1** ($x_{i+1} = (513 \cdot x_i + \text{0x1B0CA37A4BA9}) \pmod{2^{48}}$).
- **Alias State Machine (`AliasManager`)**: Full lifecycle (`INHIBITED` $\to$ `CHECKING` $\to$ `PERMITTED`), 100 ms conflict observation timer, active defense transmitting `RID` against colliding newcomers, and graceful release with `AMR`.

### Step 3: Event Transport Protocol (`pylcc.event_id`, `pylcc.events`)
- **Event ID Model (`EventID`)**: 64-bit unique identifier (`05.01.01.01.14.00.00.01`).
- **Event Manager (`EventManager`)**: Producer/Consumer registry broadcasting Producer/Consumer Event Reports (**PCER** `0x05B4`).
- **Discovery & Query Handling**: Responds to **Identify Consumer** (`0x08F4`), **Identify Producer** (`0x0914`), and **Identify Events Global** (`0x0976`) / **Addressed** (`0x0968`).

### Step 4: Datagram Transport & SNIP Engine (`pylcc.datagram`, `pylcc.snip`)
- **Datagram Transport Protocol (`DatagramManager`) (NMRA S-9.7.3.2 / S-9.7.2.1)**:
  - Multi-frame fragmentation and reassembly up to 72 bytes across 8-byte CAN frames (Formats `0x1A`, `0x1B`, `0x1C`, `0x1D`).
  - Flow control with automatic `Datagram Received OK` (`0x0A28`) and `Datagram Rejected` (`0x0A48`) NAK with error codes.
  - Asynchronous `send_datagram()` with timeout and ACK confirmation.
- **SNIP & Protocol Identification (`SnipService`) (NMRA S-9.7.4.3 / S-9.7.3)**:
  - `SnipData`: Stores Manufacturer, Model, Hardware/Software versions, and User Name & Description.
  - Streams SNIP replies (`0x0A08`) in response to inquiry (`0x0DE8`).
  - Protocol Support Inquiry (**PIP** `0x0828` $\to$ `0x0668`).

### Step 5: Memory Configuration Protocol & CDI XML (`pylcc.memory`, `pylcc.cdi`)
- **Memory Configuration Engine (`MemoryConfigurationService`) (NMRA S-9.7.4.2)**:
  - Handles Datagram Protocol `0x20` commands:
    - **Memory Read** (`0x40` generic, `0x41` CDI, `0x42` All-Memory, `0x43` Config Memory) $\to$ **Read Reply** (`0x50`..`0x53`) or Failure (`0x58`).
    - **Memory Write** (`0x00`, `0x03`) $\to$ **Write Reply** (`0x10`, `0x13`) or Failure (`0x18`).
    - **Get Configuration Options** (`0x80` $\to$ `0x82`).
    - **Get Space Info** (`0x84` $\to$ `0x86`).
    - **Node Restart / Reboot** (`0xA8`).
- **Programmatic CDI XML Generator (`CdiDocument`) (NMRA S-9.7.4.1)**:
  - Generates valid OpenLCB XML compliant with schema `http://openlcb.org/schema/cdi/1/1/cdi.xsd`.
  - Supports `CdiSegment`, `CdiGroup` (with replication), `CdiEventID`, `CdiInt` (with min, max, default, relation dropdown maps), and `CdiString`.
  - **Automatic Memory Offset Calculation**: Sequentially assigns addresses across segments and replicated groups.
  - **Flash Persistence**: Populates default configuration binaries and loads/saves settings to flash storage (`config.bin`).
  - Serves null-terminated UTF-8 byte stream required by JMRI.

### Step 6: Unified Node Orchestrator & Layout Components (`pylcc.node`, `pylcc.components`)
- **Unified Node (`Node`)**:
  - Orchestrates Transport, Alias, Events, Datagrams, SNIP, Memory Config, and attached components.
  - Single-task sequential frame dispatch loop cutting MicroPython Task heap allocations by 75%.
  - Automatic CDI generation: Auto-creates CDI schema from registered components if no explicit document is supplied.
  - Broadcasts `Initialization Complete` (`0x0100`) upon acquiring CAN alias.
- **Pluggable Component Reference Stubs (supports `LCC*` prefixes to prevent collisions with user drivers)**:
  - **`LCCTurnout` / `Turnout`**: Consumes Close/Throw events; produces feedback; hooks into `on_change`.
  - **`LCCSensor` / `Sensor` / `LCCBlockDetector`**: Produces Active/Inactive events; responds to Identify Producer queries.
  - **`LCCSignal` / `Signal` / `LCCSignalMast`**: Consumes aspect events (`STOP`, `APPROACH`, `CLEAR`, etc.); notifies via `on_aspect_change`.
  - **`LCCOutput` / `Output` / `LCCLight`**: Consumes discrete On/Off commands; hooks into `on_change`.

---

## Validation & Testing

### Running Automated Unit Tests
The project includes a comprehensive test suite covering all layers:
```bash
python3 -m unittest discover tests
```
*Current test suite: **75 automated tests (100% passing)**.*

### MicroPython Bytecode Compilation Verification
All core modules are verified to compile cleanly with `mpy-cross` without desktop-only dependencies:
```bash
for f in pylcc/*.py pylcc/transport/*.py pylcc/components/*.py; do
    mpy-cross "$f"
done
```

---

## Running the Examples

### 1. Start a local GridConnect TCP Hub:
```bash
python3 examples/mock_lcc_hub.py 127.0.0.1 12021
```

### 2. Connect an OpenLCB Traffic Monitor:
Decodes Alias allocation, Event PCERs, Datagrams, SNIP node queries, and PIP capability replies in real time:
```bash
python3 examples/gridconnect_tcp_monitor.py 127.0.0.1 12021
```

### 3. Launch a Virtual Turnout & Sensor Node:
Demonstrates `LCCNode` with `LCCTurnout` and `LCCSensor`, automatic CDI generation, and periodic block occupancy simulation:
```bash
python3 examples/virtual_turnout_node.py 127.0.0.1 12021
```

### 4. Run the Full Interactive JMRI / Multi-I/O Node:
Features Turnouts, Track Sensors, a 3-Aspect Signal Mast, Yard Lighting, and custom CDI XML with configurable servo speeds and debounce delays:
```bash
python3 examples/jmri_interactive_node.py 127.0.0.1 12021
```

### 5. Run a Wireless Node on WiFi Microcontrollers (ESP32 / Pico W):
Connects directly to your WiFi network and communicates LCC over TCP with JMRI or an LCC Hub (no CAN transceivers required):
```python
# Upload to ESP32 or Pico W and run in MicroPython:
# python -m mpremote run examples/micropython_wifi_node.py
```

### 6. Run a Hardware WiFi <-> CAN Bridge Gateway (`LCCBridge`):
Bridges your physical CAN bus (125 kbps) with wireless TCP clients (e.g. JMRI) on an ESP32 or Pico W + MCP2518FD:
```python
# Upload to ESP32 or Pico W and run in MicroPython:
# python -m mpremote run examples/wifi_can_bridge.py
```

---

## Project Roadmap

See [TODO.md](TODO.md) for the detailed implementation roadmap and completed milestones (Steps 1–7 complete; Step 8 packaging next).
