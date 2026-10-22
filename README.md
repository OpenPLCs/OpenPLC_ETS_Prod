# OpenPLC ETS product

The ETS product file for using the OpenPLC board as a KNX TP device, its source,
and how to use the board's KNX module.

| File | What it is |
|---|---|
| `OpenPLC_TP.knxprod` | The signed product, imported into ETS |
| `knx_objects.json` | Which board port maps to which group object; the only place to edit |
| `OpenPLC_TP.xml` | Generated from `knx_objects.json` and `tools/product_template.xml`; do not edit |
| `tools/gen_product.py` | Generates the XML, signs the `.knxprod`, writes the board package header (Windows only) |
| `tools/OpenKNXproducer/` | OpenKNXproducer v4.3.12 for Windows x64, which signs the product (GPL-3.0, see its `README.md`) |
| `tools/ets_paths.json` | Where to look for ETS; the only file with absolute paths. Edit it if ETS is installed elsewhere |

## Status

It imports into ETS 5.7. On 2026-10-12 ETS downloaded application version 4 to a board
running `KNX_Temperature` on a real TP bus; the board then received the bus temperature
and switched another device. The other examples are not yet validated on a real bus.

On 2026-10-13 the project device was updated in place from version 4 to the current
version 5 (`MaxEntries` 2600) and downloaded to the board with its 16 KiB KNX data area.

## Using the KNX module

The board is a KNX TP device (mask `0x07B0`). ETS reaches the bus through a KNX IP
interface or gateway on the network.

1. **Sketch.** The sketch declares the role with `OPENPLC_KNX_ROLE(TP_DEVICE);` (the
   KNX examples already do); upload it to the board.
2. **Import.** In ETS: *Catalogs → Import*, choose `OpenPLC_TP.knxprod`.
3. **Add the device** from the catalog to a TP line in your project. ETS lists it
   under the manufacturer *KNX Association* (the shared test manufacturer ID
   `M-00FA`); search for `OpenPLC TP` or the order number `OPENPLC-TP`.
4. **Individual address.** While the sketch runs, press the BOOT0 button once: the
   system LED stays on and the RS232 port (terminals C05 / C06) prints `KNX: programming mode on`.
   Let ETS program the address; ETS or a second press ends programming mode.
   **Do not hold the button while powering up or resetting the board**: that enters
   the bootloader's upload mode, and holding it 10 s restores factory state.
5. **Download** the application from ETS. ETS restarts the board after programming the
   address and at the end of every download; each restart passes through the bootloader's
   2 s startup window (system LED blinking) before the sketch runs again.
6. **Group addresses.** Link a group address to each object:

| Objects | Name | Type | Flags |
|---|---|---|---|
| 1, 3, 5, 7, 9, 11 | Relay 1-6 switch | DPT 1.001, 1 bit | communicate, write |
| 2, 4, 6, 8, 10, 12 | Relay 1-6 status | DPT 1.001, 1 bit | communicate, read, transmit |
| 13-20 | DI 1-8 | DPT 1.001, 1 bit | communicate, read, transmit |
| 21 | Temperature | DPT 9.001, 2 bytes | communicate, write, transmit, update |
| 22 | Temperature switch | DPT 1.001, 1 bit | communicate, read, transmit |
| 23 | AI 1 voltage | DPT 9.020 (mV), 2 bytes | communicate, read, transmit |
| 24 | AI 2 current | DPT 9.021 (mA), 2 bytes | communicate, read, transmit |
| 25, 26 | AO 1-2 setpoint | DPT 9.021 (mA), 2 bytes | communicate, write |

Object numbers never change once published; new objects are added after the last one,
so existing ETS projects stay valid.

`MaxEntries` of the address and association tables (2600 each) is what fits the board's
16384-byte KNX data area: 84 B stack metadata + 4 B parameters + (2 + 2 × objects) B group
object table + (2 + 2 × addresses) B + (2 + 4 × associations) B, each block rounded up to 4,
allows 2705; 2600 leaves room for more objects.

All examples share this one product, each using some of its objects, so switching
examples only needs the new sketch uploaded; the ETS configuration stays on the board.

## Object map

`knx_objects.json` holds the application version and every group object:

```json
{
  "application_version": 5,
  "replaces_versions": [1, 2, 3, 4],
  "order_number": "OPENPLC-TP",
  "objects": [
    {"number": 1, "key": "RELAY1_SWITCH", "name": "Relay 1 switch", "function": "Switch",
     "port": "RY1", "size": "1 Bit", "dpt": "DPST-1-1", "flags": "CW", "note": ""}
  ]
}
```

| Field | Meaning |
|---|---|
| `number` | Group object number in ETS; never change an existing one |
| `key` | Upper-case C name; the header defines `KNX_OBJ_<key>` |
| `port` | Board terminal (`RY1`, `DI1`, `AI1`, `AO1`), or `null` for values that only live on the bus |
| `flags` | ETS flags: `C` communicate, `R` read, `W` write, `T` transmit, `U` update, `I` read on init |
| `note` | Free text; JSON has no comments |

`python tools/gen_product.py` (or double-click `tools/gen_product.cmd`):

- writes `OpenPLC_TP.xml`, signs it into `OpenPLC_TP.knxprod` with the bundled `tools/OpenKNXproducer/OpenKNXproducer-x64.exe` (`--producer` to use another) and the local ETS 5.7, and writes `open_plc_arduino/libraries/OpenPLC_KNX/src/openplc_knx_objects.h` (`--core` to point elsewhere; `--no-sign` skips signing);
- stops without writing anything when `application_version` is missing;
- warns, and still generates, when an existing object's number, DPT or flags changed or an object was removed (group address links in users' ETS projects shift), and when objects changed but `application_version` did not; adding objects is silent;
- looks for ETS in the folders listed in `tools/ets_paths.json`, and reports "only ETS 5.7 is supported for now" when it finds ETS6 but no ETS 5.7.

Every other path the tool uses is relative to this repository; `--core` defaults to `../open_plc_arduino`.

The header defines `OPENPLC_KNX_ORDER_NUMBER`, `OPENPLC_KNX_APP_VERSION` and one `KNX_OBJ_<key>` per object; examples take object numbers only from it.

## Regenerating the product

The product is generated with [OpenKNXproducer](https://github.com/OpenKNX/OpenKNXproducer)
v4.3.12, bundled in `tools/OpenKNXproducer/`. It signs with the DLLs of the locally installed ETS, so it only runs on a
machine with ETS installed. With ETS 5.7, the source must use the namespace
`http://knx.org/xml/project/20`.

`tools/gen_product.py` runs it as

```
OpenKNXproducer-x64.exe knxprod -o OpenPLC_TP.knxprod OpenPLC_TP.xml
```

and writes the IDs from `tools/product_template.xml`: the last four digits stay `0000`
(signing replaces them with a hash), the version field (`M-00FA_A-0001-05-0000`) comes from
`application_version`, and the order number is written with every character other than a
letter or digit as `.` plus its hex code (`OPENPLC-TP` becomes `OPENPLC.2DTP`), or ETS
imports the product but cannot list it. Any change to the product's content raises
`application_version` by one, since ETS identifies an application by number and version;
every new version also lists all earlier ones in `replaces_versions`, otherwise ETS greys
out *Update* under the device's *Properties → Information → Application Program*.

Exception: on 2026-10-13 version 5 was amended in place (adding `ReplacesVersions`) because
no device had it yet; to re-import it, first delete the old version 5 from the ETS catalog.

Commit `knx_objects.json`, the regenerated `OpenPLC_TP.xml` and `.knxprod`, and the board package header together.
