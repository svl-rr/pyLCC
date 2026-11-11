# pyLCC

A lightweight, modular **OpenLCB / NMRA LCC (Layout Command Control)** protocol stack designed to run on **MicroPython** microcontrollers as well as desktop Python.

---

## Target Platforms & Hardware Interfaces

1. **ESP32-C6**:
   - **Native Hardware CAN**: Built-in TWAI (Two-Wire Automotive Interface / CAN 2.0B) controller + external 3.3V transceiver (e.g. SN65HVD230).
   - **Network**: WiFi GridConnect over TCP.
2. **Raspberry Pi Pico / Pico 2 (RP2040 / RP2350)**:
   - **Hardware CAN**: **MCP2517FD / MCP2518FD** over high-speed SPI (with deep FIFO to prevent buffer overflow under LCC bus bursts; legacy MCP2515 is not supported due to shallow 2-buffer overflow limitations).
   - **Network**: WiFi GridConnect over TCP (Pico W).
3. **STM32**:
   - **Native Hardware CAN**: Built-in on-chip CAN / FDCAN controller using MicroPython's native interface (`pyb.CAN` / `machine.CAN`).
4. **Desktop Python (macOS / Linux / Windows)**:
   - Run virtual nodes, hubs, test monitors, and unit test suites directly against JMRI or physical adapters.

---

## Step 1 Completed: Frame & Transport Abstraction

- **CAN Frame Model (`pylcc.can.CanFrame`)**:
  - Full 29-bit extended CAN identifier packing and unpacking compliant with **NMRA S-9.7.2.1**.
  - Direct bitfield extraction for `source_alias`, `variable_field`, `is_openlcb_message`, and `is_can_control`.
  - First-class constructors and parsers for CAN Control Frames:
    - **CID 7, 6, 5, 4** (Check ID)
    - **RID** (Reserve ID)
    - **AMD** (Alias Map Definition)
    - **AME** (Alias Mapping Enquiry)
    - **AMR** (Alias Map Reset)
- **GridConnect ASCII Serialization & Parsing (`pylcc.gridconnect`)**:
  - Serialization: `:X195B422AN0102030405060708;`
  - Stream Parser: Zero-crash, resilient stream buffer handling chunks, fragmented packets, garbage recovery, and multiple frames in a single read.
- **Transport Abstraction (`pylcc.transport`)**:
  - `BaseTransport`: Unified async send/receive interface and event listener callbacks.
  - `LoopbackTransport` & `LoopbackBus`: In-memory bus for instant unit testing and simulation.
  - `GridConnectTcpClient`: Asynchronous TCP client connecting to JMRI or any OpenLCB WiFi Hub.
  - `GridConnectTcpServer`: Lightweight TCP hub to connect multiple physical nodes or JMRI directly.
- **Cross-Compilation Verified**:
  - All source files verified and compile cleanly with `mpy-cross` for MicroPython execution.

---

## Step 2 Completed: CAN Alias Allocation Protocol

- **Node ID Model (`pylcc.node_id.NodeID`)**:
  - 48-bit globally unique identifier representation (`05.01.01.01.14.00`).
  - Fast bitfield extraction of 12-bit chunks for CID 7, 6, 5, 4.
- **48-Bit PRNG (`pylcc.alias.AliasPrng`)**:
  - Implements the Heidi G. Kuehn (1961) PRNG algorithm specified in OpenLCB **TN-9.7.2.1**:
    $x_{i+1} = (513 \cdot x_i + 0x1B0CA37A4BA9) \pmod{2^{48}}$.
  - Generates non-zero, well-distributed 12-bit CAN aliases even across sequential manufacturer Node IDs.
- **Alias State Machine (`pylcc.alias.AliasManager`)**:
  - Full lifecycle: `INHIBITED` $\to$ `CHECKING` $\to$ `PERMITTED`.
  - Transmits CID sequence 7 $\to$ 6 $\to$ 5 $\to$ 4 with a conflict observation timer (100 ms).
  - Handles conflicts: steps the PRNG and retries on collision.
  - Claims alias with **RID** and announces binding with **AMD** containing the 6-byte Node ID.
  - **Active Defense**: Defends claimed alias against newcomers probing with CID by immediately transmitting RID.
  - Responds to **AME** (Alias Mapping Enquiry) with AMD.
  - Releases alias on node shutdown with **AMR**.

---

## Step 3 Completed: Event Transport (Producer / Consumer Model)

- **Event ID Model (`pylcc.event_id.EventID`)**:
  - 64-bit globally unique event identifier representation (`05.01.01.01.14.00.00.01`).
  - Helper `EventID.from_node_id(node_id, index)` to partition local event spaces.
- **Event Manager (`pylcc.events.EventManager`)**:
  - **Consumers**: Registers local listeners with sync or async callbacks invoked when a matching **PCER** (Producer/Consumer Event Report) frame is received.
  - **Producers**: Broadcasts **PCER** (`0x05B4`) across the bus when layout state changes (e.g. block detector occupancy, push buttons).
  - **Event Inquiries & Discovery**:
    - Responds to **Identify Consumer** (`0x08F4`) with `Consumer Identified` (`Valid`, `Invalid`, or `Unknown`).
    - Responds to **Identify Producer** (`0x0914`) with `Producer Identified` (`Valid`, `Invalid`, or `Unknown`).
    - Responds to **Identify Events Global** (`0x0976`) and **Identify Events Addressed** (`0x0968`) by enumerating all local producers and consumers to JMRI or network tools.

---

## Running the Unit Tests

```bash
python3 -m unittest discover tests
```

---

## Running the Examples

### 1. Start a local GridConnect TCP Hub:
```bash
python3 examples/mock_lcc_hub.py 127.0.0.1 12021
```

### 2. Connect an OpenLCB Traffic Monitor:
```bash
python3 examples/gridconnect_tcp_monitor.py 127.0.0.1 12021
```
