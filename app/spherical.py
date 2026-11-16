"""Inject 360° metadata so YouTube / VLC / players treat the file as spherical.

Writes both Google Spherical Video V1 (XMP 'uuid' box in the video trak)
and V2 ('st3d' + 'sv3d' boxes inside the video sample entry), the same
data Google's spatial-media injector writes. With spatial audio, an 'SA3D'
box (first-order ambiX: ACN order, SN3D) goes into the audio sample entry.
Also: GPano XMP for photos.
"""
from __future__ import annotations

import os
import struct

SPHERICAL_UUID = bytes.fromhex("ffcc8263f8554a938814587a02521fdd")
SOFTWARE = "FusionStitch"

V1_XML = (
    '<?xml version="1.0"?><rdf:SphericalVideo\n'
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
    'xmlns:GSpherical="http://ns.google.com/videos/1.0/spherical/">'
    "<GSpherical:Spherical>true</GSpherical:Spherical>"
    "<GSpherical:Stitched>true</GSpherical:Stitched>"
    f"<GSpherical:StitchingSoftware>{SOFTWARE}</GSpherical:StitchingSoftware>"
    "<GSpherical:ProjectionType>equirectangular</GSpherical:ProjectionType>"
    "</rdf:SphericalVideo>"
)

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta"}
SAMPLE_ENTRIES = {b"hvc1", b"hev1", b"avc1", b"avc3"}
AUDIO_ENTRIES = {b"mp4a", b"Opus", b"lpcm", b"sowt", b"twos", b"ipcm", b"fpcm"}


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _full(typ: bytes, payload: bytes, version=0, flags=0) -> bytes:
    return _box(typ, struct.pack(">I", (version << 24) | flags) + payload)


def _v2_boxes() -> bytes:
    st3d = _full(b"st3d", b"\x00")  # monoscopic
    svhd = _full(b"svhd", SOFTWARE.encode() + b"\x00")
    prhd = _full(b"prhd", struct.pack(">iii", 0, 0, 0))
    equi = _full(b"equi", struct.pack(">IIII", 0, 0, 0, 0))
    sv3d = _box(b"sv3d", svhd + _box(b"proj", prhd + equi))
    return st3d + sv3d


def sa3d_box(channels=4) -> bytes:
    """Google spatial-media SpatialAudioBox: version 0, periphonic (type 0),
    order 1, ACN channel ordering (0), SN3D normalization (0), map 0..n-1."""
    payload = struct.pack(">BBIBBI", 0, 0, 1, 0, 0, channels)
    payload += b"".join(struct.pack(">I", i) for i in range(channels))
    return _box(b"SA3D", payload)


def _audio_entry_fixed(e: bytes, hdr: int) -> int:
    """Length of the AudioSampleEntry fields before the child boxes."""
    version = struct.unpack(">H", e[hdr + 8:hdr + 10])[0]  # QuickTime sound description version
    return 28 + {1: 16, 2: 36}.get(version, 0)


def _iter(buf: bytes, start: int, end: int):
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[i:i + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - i
        yield i, size, hdr, typ
        i += size


def _rewrite(buf: bytes, typ: bytes, start: int, end: int, ctx: dict) -> bytes:
    """Rebuild the children of a container, injecting boxes on the way."""
    out = []
    for i, size, hdr, t in _iter(buf, start, end):
        body_s, body_e = i + hdr, i + size
        if t == b"trak":
            kind = _handler(buf, body_s, body_e)
            sub = {"is_video": kind == b"vide", "is_audio": kind == b"soun",
                   "shift": ctx.get("shift"), "spatial_audio": ctx.get("spatial_audio")}
            payload = _rewrite(buf, t, body_s, body_e, sub)
            if sub["is_video"]:
                payload += _box(b"uuid", SPHERICAL_UUID + V1_XML.encode())
            out.append(_box(t, payload))
        elif t in CONTAINERS:
            out.append(_box(t, _rewrite(buf, t, body_s, body_e, ctx)))
        elif t == b"stsd" and ctx.get("is_video"):
            head = buf[body_s:body_s + 8]  # version/flags + entry count
            entries = []
            for j, esize, ehdr, et in _iter(buf, body_s + 8, body_e):
                e = buf[j:j + esize]
                if et in SAMPLE_ENTRIES:
                    fixed = e[ehdr:ehdr + 78]  # VisualSampleEntry fields
                    kids = b"".join(buf[k:k + ks] for k, ks, _, kt in _iter(buf, j + ehdr + 78, j + esize)
                                    if kt not in (b"st3d", b"sv3d"))
                    e = _box(et, fixed + kids + _v2_boxes())
                entries.append(e)
            out.append(_box(t, head + b"".join(entries)))
        elif t == b"stsd" and ctx.get("is_audio") and ctx.get("spatial_audio"):
            head = buf[body_s:body_s + 8]
            entries = []
            for j, esize, ehdr, et in _iter(buf, body_s + 8, body_e):
                e = buf[j:j + esize]
                if et in AUDIO_ENTRIES:
                    n = _audio_entry_fixed(e, ehdr)
                    fixed = e[ehdr:ehdr + n]
                    kids = b"".join(buf[k:k + ks] for k, ks, _, kt in _iter(buf, j + ehdr + n, j + esize)
                                    if kt != b"SA3D")
                    e = _box(et, fixed + kids + sa3d_box(ctx["spatial_audio"]))
                entries.append(e)
            out.append(_box(t, head + b"".join(entries)))
        elif t in (b"stco", b"co64") and ctx.get("shift"):
            n = struct.unpack(">I", buf[body_s + 4:body_s + 8])[0]
            fmt = ">%d%s" % (n, "I" if t == b"stco" else "Q")
            vals = struct.unpack(fmt, buf[body_s + 8:body_s + 8 + struct.calcsize(fmt)])
            vals = [v + ctx["shift"] for v in vals]
            out.append(_box(t, buf[body_s:body_s + 8] + struct.pack(fmt, *vals)))
        elif t == b"uuid" and buf[body_s:body_s + 16] == SPHERICAL_UUID:
            continue  # drop old V1 box, a fresh one is appended
        else:
            out.append(buf[i:i + size])
    return b"".join(out)


def _handler(buf, s, e):
    for i, size, hdr, t in _iter(buf, s, e):
        if t == b"mdia":
            for j, sz, h, tt in _iter(buf, i + hdr, i + size):
                if tt == b"hdlr":
                    return buf[j + h + 8:j + h + 12]
    return None


def inject_mp4(path: str, spatial_audio: int = 0) -> None:
    """spatial_audio: number of ambiX channels (4) to tag the audio track with, 0 = none."""
    with open(path, "rb") as f:
        data_len = os.fstat(f.fileno()).st_size
        boxes = []
        pos = 0
        while pos < data_len:
            f.seek(pos)
            hdr = f.read(16)
            size, typ = struct.unpack(">I4s", hdr[:8])
            if size == 1:
                size = struct.unpack(">Q", hdr[8:16])[0]
            elif size == 0:
                size = data_len - pos
            boxes.append((pos, size, typ))
            pos += size
        moov = next(b for b in boxes if b[2] == b"moov")
        mdat_before_moov = any(b[2] == b"mdat" and b[0] < moov[0] for b in boxes)
        f.seek(moov[0])
        moov_buf = f.read(moov[1])

    def build(shift):
        return _box(b"moov", _rewrite(moov_buf, b"moov", 8, len(moov_buf), {"shift": shift, "spatial_audio": spatial_audio}))

    new_moov = build(0)
    if mdat_before_moov and moov is boxes[-1]:
        # common case: moov is the last box -> rewrite tail in place
        with open(path, "r+b") as f:
            f.seek(moov[0])
            f.write(new_moov)
            f.truncate()
        return
    # moov sits before the media data: chunk offsets must move
    delta = len(new_moov) - moov[1]
    new_moov = build(delta)
    tmp = path + ".tmp"
    with open(path, "rb") as src, open(tmp, "wb") as dst:
        for pos, size, typ in boxes:
            if typ == b"moov":
                dst.write(new_moov)
                continue
            src.seek(pos)
            left = size
            while left:
                chunk = src.read(min(left, 1 << 24))
                dst.write(chunk)
                left -= len(chunk)
    os.replace(tmp, path)


def gpano_xmp(width: int, height: int) -> bytes:
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:GPano="http://ns.google.com/photos/1.0/panorama/">'
        "<GPano:UsePanoramaViewer>True</GPano:UsePanoramaViewer>"
        "<GPano:ProjectionType>equirectangular</GPano:ProjectionType>"
        f"<GPano:CroppedAreaImageWidthPixels>{width}</GPano:CroppedAreaImageWidthPixels>"
        f"<GPano:CroppedAreaImageHeightPixels>{height}</GPano:CroppedAreaImageHeightPixels>"
        f"<GPano:FullPanoWidthPixels>{width}</GPano:FullPanoWidthPixels>"
        f"<GPano:FullPanoHeightPixels>{height}</GPano:FullPanoHeightPixels>"
        "<GPano:CroppedAreaLeftPixels>0</GPano:CroppedAreaLeftPixels>"
        "<GPano:CroppedAreaTopPixels>0</GPano:CroppedAreaTopPixels>"
        f"<GPano:StitchingSoftware>{SOFTWARE}</GPano:StitchingSoftware>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    ).encode()
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def inject_jpeg(path: str, width: int, height: int) -> None:
    with open(path, "rb") as f:
        data = f.read()
    assert data[:2] == b"\xff\xd8"
    with open(path, "wb") as f:
        f.write(data[:2] + gpano_xmp(width, height) + data[2:])
