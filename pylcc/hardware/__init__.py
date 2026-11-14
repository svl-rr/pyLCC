"""
pyLCC Hardware Transports and Device Drivers.
Modular, platform-specific drivers for target microcontrollers.

Drivers are isolated to conserve flash and RAM on target microcontrollers:
- esp32_twai: Native ESP32 / ESP32-C6 TWAI (Classical CAN 2.0B) peripheral.
- mcp2518fd: High-speed SPI driver for RP2040 / RP2350 (Raspberry Pi Pico / Pico 2).
- stm32_can: Native on-chip CAN driver for STM32 (pyb.CAN).
"""

__all__ = []
