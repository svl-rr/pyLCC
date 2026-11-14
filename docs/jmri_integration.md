# pyLCC & JMRI Integration Guide

This guide walks you through integrating `pyLCC` nodes with **JMRI (Java Model Railroad Interface)** for visual layout control, turnout operation, block detection, and configuration using JMRI's **DecoderPro CDI Tool**.

---

## 1. Setting Up JMRI Connection

`pyLCC` speaks standard **GridConnect over TCP**, the native protocol supported by JMRI's OpenLCB / LCC adapter.

### In JMRI Preferences:
1. Open JMRI (PanelPro or DecoderPro).
2. Go to **Edit > Preferences > Connections**.
3. Click **+ Add Connection**:
   - **System Manufacturer**: `OpenLCB`
   - **System Connection**: `OpenLCB Network Interface`
   - **Connection Type**: `GridConnect Network Interface`
   - **Server / Host IP**: Enter the IP address of your `pyLCC` node (or `127.0.0.1` if testing locally with `mock_lcc_hub.py`).
   - **Port**: `12021` (default OpenLCB / LCC port)
4. Click **Save** and restart JMRI.

---

## 2. Node Discovery & Identification (SNIP / PIP)

Once connected, JMRI automatically queries the bus:
1. In JMRI, open the menu: **LCC > Configure Nodes** (or **OpenLCB > Configure Nodes**).
2. In the node tree, you will see your `pyLCC` node listed with its 48-bit Node ID (e.g., `05.01.01.01.14.05`).
3. Click on the node:
   - **Manufacturer**: e.g., `SVL Layout Systems`
   - **Model**: e.g., `pyLCC Virtual 2T-2S`
   - **Software Version**: `0.1.0`
   - **User Name**: Displays the user-assigned node name from `SnipData`.

---

## 3. Configuring Nodes via JMRI DecoderPro CDI Tool

`pyLCC` generates and serves **CDI XML (Configuration Description Information)** directly over Datagram Memory Space `0xFF` (NMRA Standard S-9.7.4.1).

1. In the **Configure Nodes** window, click **Open Configuration dialog**.
2. JMRI will automatically read the CDI XML from the node.
3. A graphical configuration window will open showing all registered groups and elements:
   - **Node Description**: Editable text box.
   - **Turnouts**: Shows configured Close and Throw Event IDs.
   - **Sensors**: Shows configured Active and Inactive Event IDs.
   - **Parameters**: Sliders and dropdowns (e.g. Servo Speed, Debounce Delay in milliseconds).
4. When you click **Write** in JMRI:
   - JMRI sends Datagram Write commands (Protocol `0x20`) to Memory Space `0xFD`.
   - `pyLCC` updates its memory space and automatically persists the changes to `config.bin` (in LittleFS flash storage).

---

## 4. Operating Turnouts in JMRI

1. In JMRI, open **Tools > Tables > Turnouts**.
2. Click **Add...**:
   - **Hardware Address**: Enter the Event ID string configured on your `pyLCC` node:
     `05.01.01.01.14.05.00.01;05.01.01.01.14.05.00.02`
     *(Format: `CloseEvent;ThrowEvent`)*
   - **User Name**: e.g. `Mainline Crossover`
3. Click **OK**.
4. In the table, clicking **Closed** or **Thrown** will broadcast standard OpenLCB `PCER` frames. Your `pyLCC` node will trigger its `on_change` callback, move the physical servo/switch machine, and produce feedback!

---

## 5. Monitoring Sensors & Block Detectors in JMRI

1. In JMRI, open **Tools > Tables > Sensors**.
2. Click **Add...**:
   - **Hardware Address**: Enter the Event ID string:
     `05.01.01.01.14.05.00.10;05.01.01.01.14.05.00.11`
     *(Format: `ActiveEvent;InactiveEvent`)*
   - **User Name**: e.g. `Block 1 Detector`
3. Click **OK**.
4. When train wheels bridge the track detector on your layout, `pyLCC` broadcasts the `PCER` active event. The indicator in JMRI instantly turns from **Inactive** to **Active** (Red)!

---

## 6. Using pyLCC as a WiFi-to-CAN Hardware Gateway

If you run [`examples/wifi_can_bridge.py`](../examples/wifi_can_bridge.py) on an **ESP32** or **Raspberry Pi Pico W**:
- Connect the ESP32 to your physical LCC layout bus.
- Connect JMRI wirelessly to the ESP32's IP address on port `12021`.
- JMRI will see **every physical node** on your layout through the `pyLCC` gateway with zero USB cables attached to your computer!
