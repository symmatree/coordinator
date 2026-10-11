"""Single source of truth for the PPS distribution board: parts + nets."""

# ref -> (lib_id, value, footprint, x_mm, y_mm)
PARTS = {
  "J1": ("Connector_Generic:Conn_02x04_Odd_Even", "Coordinator hdr 17-24",
         "Connector_PinHeader_2.54mm:PinHeader_2x04_P2.54mm_Vertical", 40, 40),
  "J2": ("Connector_Generic:Conn_01x07", "RTC BOB-10160 JP1",
         "Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical", 40, 110),
  "R1": ("Device:R", "2.2k", "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", 80, 100),
  "C1": ("Device:C", "0.1uF", "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm", 100, 110),
  "U1": ("PPS:SN74AHC125N", "SN74AHC125N", "Package_DIP:DIP-14_W7.62mm_Socket", 150, 100),
  "C2": ("Device:C", "0.1uF", "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm", 175, 100),
  "U2": ("PPS:SN74AHC125N", "SN74AHC125N", "Package_DIP:DIP-14_W7.62mm_Socket", 240, 50),
  "C3": ("Device:C", "0.1uF", "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm", 265, 50),
  "U3": ("PPS:SN74AHC125N", "SN74AHC125N", "Package_DIP:DIP-14_W7.62mm_Socket", 240, 160),
  "C4": ("Device:C", "0.1uF", "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm", 265, 160),
  "R2": ("Device:R", "1k", "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", 300, 195),
  "D1": ("Device:LED", "proof-of-life", "LED_THT:LED_D5.0mm", 300, 215),
  "J3": ("Connector_Generic:Conn_01x02", "PPS -> FC", "Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", 340, 40),
  "J4": ("Connector_Generic:Conn_01x02", "PPS -> NE campod", "Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", 340, 65),
  "J5": ("Connector_Generic:Conn_01x02", "PPS -> NW campod", "Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", 340, 90),
  "J6": ("Connector_Generic:Conn_01x02", "PPS -> SE campod", "Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", 340, 150),
  "J7": ("Connector_Generic:Conn_01x02", "PPS -> SW campod", "Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", 340, 175),
}

# net -> [(ref, pin), ...]
NETS = {
  "+3V3":      [("J1","1"), ("J2","6"), ("R1","2"), ("C1","1"),
                ("U1","14"), ("C2","1"), ("U2","14"), ("C3","1"), ("U3","14"), ("C4","1")],
  "GND":       [("J1","4"), ("J2","7"), ("C1","2"),
                ("U1","7"), ("U1","1"), ("U1","4"), ("U1","10"), ("U1","13"), ("C2","2"),
                ("U2","7"), ("U2","1"), ("U2","4"), ("U2","10"), ("U2","13"), ("C3","2"),
                ("U3","7"), ("U3","1"), ("U3","4"), ("U3","10"), ("U3","13"), ("C4","2"),
                ("D1","1"),
                ("J3","2"), ("J4","2"), ("J5","2"), ("J6","2"), ("J7","2")],
  "SPI_MOSI":  [("J1","3"), ("J2","2")],
  "SPI_MISO":  [("J1","5"), ("J2","3")],
  "SPI_SCLK":  [("J1","7"), ("J2","4")],
  "SPI_CS":    [("J1","8"), ("J2","1")],
  "SQW":       [("J2","5"), ("R1","1"), ("U1","2"), ("U1","5"), ("U1","9"), ("U1","12")],
  "T2_FRONT":  [("U1","3"), ("U2","2"), ("U2","5"), ("U2","9"), ("U2","12")],
  "T2_REAR":   [("U1","6"), ("U3","2"), ("U3","5"), ("U3","9"), ("U3","12")],
  "T1_SPARE1": [("U1","8")],
  "T1_SPARE2": [("U1","11")],
  "PPS_FC":    [("U2","3"), ("J3","1")],
  "PPS_COORD": [("U2","6"), ("J1","2")],
  "PPS_NE":    [("U2","8"), ("J4","1")],
  "PPS_NW":    [("U2","11"), ("J5","1")],
  "PPS_SE":    [("U3","3"), ("J6","1")],
  "PPS_SW":    [("U3","6"), ("J7","1")],
  "LED_DRV":   [("U3","8"), ("R2","1")],
  "LED_A":     [("R2","2"), ("D1","2")],
  "U3_SPARE":  [("U3","11")],
}

NOTES = {
  "+3V3": "from Coordinator header pin 17; RTC and all buffers share this rail",
  "GND":  "all OE pins tied low so every channel is enabled",
  "SQW":  "open-drain; R1 parallels the breakout's own 10k. Rising edge is RC, falling is driven",
  "T1_SPARE1": "first-tier spare output, live, for a future second-tier package",
  "T1_SPARE2": "first-tier spare output, live, for a future second-tier package",
  "U3_SPARE": "rear distributor spare output",
  "PPS_COORD": "returns on header pin 18 = BCM GPIO24",
}
