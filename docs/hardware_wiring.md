# pyLCC Hardware Wiring & Connection Guide

This guide details the physical wiring and pinouts for connecting microcontrollers running `pyLCC` to an **NMRA OpenLCB / LCC** CAN bus network.

---

## 1. OpenLCB Standard CAN Bus (NMRA S-9.7.1.1)

OpenLCB networks use standard **4-pair Category 5/6 (Cat5e/Cat6) RJ45 cables** wired point-to-point in a daisy chain.

### Standard RJ45 Pinout (TIA/EIA-568-B)

| Pin | Wire Color | Signal Name | Description |
| :---: | :---: | :---: | :--- |
| **1** | White/Orange | **CAN_H** | CAN Differential High Line |
| **2** | Orange | **CAN_L** | CAN Differential Low Line |
| **3** | White/Green | **GND** | System Ground Reference |
| **4** | Blue | **V+** | Bus Power (Typically +12V to +15V DC, optional) |
| **5** | White/Blue | **V+** | Bus Power (Typically +12V to +15V DC, optional) |
| **6** | Green | **GND** | System Ground Reference |
| **7** | White/Brown | **CAN_H** | CAN Differential High (Redundant pair) |
| **8** | Brown | **CAN_L** | CAN Differential Low (Redundant pair) |

> [!IMPORTANT]
> **Bus Termination**: The physical CAN bus must be terminated with a **120 Ω (1/4 W)** resistor between `CAN_H` and `CAN_L` at the **two physical ends** of the bus chain. Do not add terminators to intermediate nodes.

---

## 2. ESP32-C6 / ESP32 Native CAN (TWAI) Wiring

The ESP32-C6 includes an on-chip Classical CAN 2.0B / TWAI controller. Because the microcontroller operates at 3.3V logic, an external **3.3V CAN Transceiver** (e.g. Texas Instruments **SN65HVD230**, **VP230**, or **MAX3051**) is required.

### Pinout Table (ESP32-C6 + SN65HVD230)

| ESP32-C6 Pin | Transceiver Pin | LCC Bus Connection | Notes |
| :--- | :--- | :--- | :--- |
| **3V3** | **VCC / 3V3** | — | 3.3V Regulated DC Power |
| **GND** | **GND** | **RJ45 Pin 3 & 6** | Common Ground |
| **GPIO 5 (TX)** | **CTX / TXD** | — | CAN Transmit output from ESP32 |
| **GPIO 4 (RX)** | **CRX / RXD** | — | CAN Receive input to ESP32 |
| — | **CAN_H** | **RJ45 Pin 1 & 7** | Differential High |
| — | **CAN_L** | **RJ45 Pin 2 & 8** | Differential Low |
| — | **RS / Slope** | Connect to GND | High-speed mode (tie to GND via 10k or direct) |

```
 +--------------------+             +------------------+
 |      ESP32-C6      |             |    SN65HVD230    |            OpenLCB Bus
 |                    |             |  3.3V CAN XCVR   |               (RJ45)
 |            3.3V O--+-------------+--O VCC           |
 |             GND O--+-------------+--O GND       CANH O================== Pin 1 (CAN_H)
 |    GPIO 5 (TX)  O--+-------------+--O TXD       CANL O================== Pin 2 (CAN_L)
 |    GPIO 4 (RX)  O--+-------------+--O RXD        GND O------------------ Pin 3 (GND)
 +--------------------+             |       RS O----+  |
                                    +---------------+--+
                                                    |
                                                   GND
```

---

## 3. Raspberry Pi Pico / Pico 2 (RP2040 / RP2350) + MCP2518FD Wiring

The RP2040 and RP2350 microcontrollers do not have built-in CAN controllers. `pyLCC` uses the **Microchip MCP2517FD / MCP2518FD** CAN FD controller connected over high-speed SPI.

> [!NOTE]
> The legacy MCP2515 is **not supported** due to its shallow 2-message buffer which overflows under LCC 125 kbps bursts. The MCP2518FD features a **31-message hardware FIFO** with zero dropped packets.

### Pinout Table (RP2040 / RP2350 + MCP2518FD SPI Breakout)

| Pico Pin (RP2040) | MCP2518FD Board Pin | LCC Bus Connection | Notes |
| :--- | :--- | :--- | :--- |
| **3V3 (Pin 36)** | **VCC / 3V3** | — | 3.3V Power |
| **GND (Pin 38)** | **GND** | **RJ45 Pin 3 & 6** | Ground |
| **GP18 (Pin 24)** | **SCK / SCLK** | — | SPI0 Clock (10–20 MHz) |
| **GP19 (Pin 25)** | **MOSI / SI** | — | SPI0 Controller Out |
| **GP16 (Pin 21)** | **MISO / SO** | — | SPI0 Controller In |
| **GP17 (Pin 22)** | **CS / NSS** | — | Chip Select (Active Low) |
| **GP20 (Pin 26)** | **INT** *(Optional)* | — | Receive Interrupt indicator |
| — | **CAN_H** | **RJ45 Pin 1 & 7** | Differential High |
| — | **CAN_L** | **RJ45 Pin 2 & 8** | Differential Low |

```
 +--------------------+             +-------------------+
 |     RP2040 Pico    |             |    MCP2518FD      |            OpenLCB Bus
 |                    |             |    SPI Module     |               (RJ45)
 |            3.3V O--+-------------+--O VCC            |
 |             GND O--+-------------+--O GND        CANH O================== Pin 1 (CAN_H)
 |    GP18 (SCK)   O--+-------------+--O SCK        CANL O================== Pin 2 (CAN_L)
 |    GP19 (MOSI)  O--+-------------+--O MOSI        GND O------------------ Pin 3 (GND)
 |    GP16 (MISO)  O--+-------------+--O MISO           |
 |    GP17 (CS)    O--+-------------+--O CS             |
 +--------------------+             +-------------------+
```

---

## 4. STM32 Native CAN Wiring (Pyboard / STM32F4 / STM32H7)

STM32 microcontrollers have on-chip CAN controllers accessible via MicroPython's `pyb.CAN`.

### Standard Pinout (STM32 CAN1)

| STM32 Pin | CAN Transceiver Pin | LCC Bus Connection | Notes |
| :--- | :--- | :--- | :--- |
| **3V3** | **VCC** | — | 3.3V Power |
| **GND** | **GND** | **RJ45 Pin 3 & 6** | System Ground |
| **PB9 (CAN1_TX)** | **TXD** | — | Transmit line |
| **PB8 (CAN1_RX)** | **RXD** | — | Receive line |
| — | **CAN_H** | **RJ45 Pin 1 & 7** | Differential High |
| — | **CAN_L** | **RJ45 Pin 2 & 8** | Differential Low |

---

## 5. Wireless / WiFi Nodes (Zero CAN Wiring)

For **ESP32** or **Raspberry Pi Pico W**, no CAN transceivers or physical cables are needed. The microcontroller connects over WiFi and exchanges GridConnect packets directly with JMRI or an LCC Hub on port `12021`:

```
 [ MicroPython Node ]  <=== WiFi ===>  [ WiFi Router ]  <=== LAN ===>  [ PC running JMRI ]
 (ESP32 or Pico W)                                                     (Port 12021)
```
See [`examples/micropython_wifi_node.py`](../examples/micropython_wifi_node.py) for setup.
