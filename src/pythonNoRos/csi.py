import struct
import time


HEADER_SIZE = 18
BANDWIDTHS = {2: 20, 3: 40, 4: 80}
E_MASK = 0x3F
R_MANT_MASK = 0x1FFC0000
I_MANT_MASK = 0x0001FFC0
R_SIGN_MASK = 0x20000000
I_SIGN_MASK = 0x00020000
COUNT_MASK = 0x400
MANT_MASK = 0x3FF


def decode_value(value):
    exponent = (value & E_MASK) - 31 + 1023
    real_exponent = exponent
    imag_exponent = exponent
    real_mantissa = (value & R_MANT_MASK) >> 18
    imag_mantissa = (value & I_MANT_MASK) >> 6

    shift = 0
    while not real_mantissa & COUNT_MASK and shift < 10:
        real_mantissa <<= 1
        shift += 1
    if shift == 10:
        real_exponent = 1023
        real_mantissa = 0
    else:
        real_exponent -= shift

    shift = 0
    while not imag_mantissa & COUNT_MASK and shift < 10:
        imag_mantissa <<= 1
        shift += 1
    if shift == 10:
        imag_exponent = 1023
        imag_mantissa = 0
    else:
        imag_exponent -= shift

    real_bits = (
        (value & R_SIGN_MASK) << 34
        | (real_mantissa & MANT_MASK) << 42
        | real_exponent << 52
    )
    imag_bits = (
        (value & I_SIGN_MASK) << 46
        | (imag_mantissa & MANT_MASK) << 42
        | imag_exponent << 52
    )
    return (
        struct.unpack("<d", struct.pack("<Q", real_bits))[0],
        struct.unpack("<d", struct.pack("<Q", imag_bits))[0],
    )


def parse_frame(data, receiver_id, ap_id):
    if len(data) < HEADER_SIZE:
        raise ValueError("incomplete CSI header")
    kk1, frame_id, rssi, frame_control = struct.unpack("<BBbB", data[:4])
    if (kk1, frame_id) != (0x11, 0x11):
        raise ValueError(
            f"invalid CSI header magic: 0x{kk1:02x} 0x{frame_id:02x}"
        )
    source_mac = data[4:10]
    sequence, csi_config, chanspec, chip = struct.unpack("<HHHH", data[10:HEADER_SIZE])
    bandwidth = BANDWIDTHS.get((chanspec >> 11) & 7)
    if bandwidth is None:
        raise ValueError("unsupported CSI bandwidth")

    subcarrier_count = int(bandwidth * 3.2)
    frame_size = HEADER_SIZE + subcarrier_count * 4
    if len(data) < frame_size:
        raise ValueError(f"incomplete CSI frame: {len(data)} of {frame_size} bytes")

    values = struct.unpack(
        f"<{subcarrier_count}I", data[HEADER_SIZE:frame_size]
    )
    decoded = [decode_value(value) for value in values]
    return {
        "schema_version": 1,
        "timestamp_ns": time.time_ns(),
        "receiver_id": receiver_id,
        "ap_id": ap_id,
        "source_mac": ":".join(f"{value:02x}" for value in source_mac),
        "sequence": sequence,
        "rssi": rssi,
        "channel": chanspec & 255,
        "bandwidth_mhz": bandwidth,
        "tx": (csi_config >> 11) & 3,
        "rx": (csi_config >> 8) & 3,
        "frame_control": frame_control,
        "frame_id": frame_id,
        "magic": kk1,
        "chip": chip,
        "n_subcarriers": subcarrier_count,
        "csi_r": [value[0] for value in decoded],
        "csi_i": [value[1] for value in decoded],
    }
