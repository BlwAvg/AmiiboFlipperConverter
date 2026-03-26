"""
amiiboconvert.py
Amiibo .bin to Flipper .nfc converter with explicit mode support

Original Code by Friendartiste
Modified by Lamp, VapidAnt, bjschafer, Lanjelin
Refactored with explicit CLI modes and enhanced file handling

Usage:
  Single file:  python3 amiibo.py --file path/to/Wario.bin -o path/to/Wario.nfc
  Directory:    python3 amiibo.py --dir path/to/bins -o path/to/output
"""
import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Tuple

# NTAG215 geometry: 135 pages of 4 bytes each = 540 bytes total
NTAG215_PAGES = 135
NTAG215_BYTES = NTAG215_PAGES * 4  # 540

# Expected config/lock page values for a standard amiibo NTAG215 chip.
EXPECTED_PAGE_130 = "01 00 0F BD"
EXPECTED_PAGE_131 = "00 00 00 04"
EXPECTED_PAGE_132 = "5F 00 00 00"

ZEROED_SIGNATURE = " ".join(["00"] * 32)


class FileValidationError(Exception):
    """Raised when a .bin file cannot be processed."""
    pass


@dataclass
class ConversionResult:
    """Result of attempting to convert a single file."""
    action: str  # 'success', 'trimmed', 'skipped_oversize', 'skipped_undersize', 'skipped_error'
    input_path: Path
    input_size: int
    output_path: Optional[Path]
    message: Optional[str]  # Warning, skip reason, or error message


def write_output(name: str, assemble: str, output_path: Path) -> None:
    """
    Write the assembled .nfc content to a file.
    :param name: The base filename without extension - e.g. for Foo.bin, Foo
    :param assemble: The converted flipper-compatible contents
    :param output_path: The target .nfc file path (must be a file, not directory)
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wt") as f:
        f.write(assemble)


def load_bin_file(
    path: Path,
    trim_oversize: bool = False,
    log_oversize_only: bool = False
) -> Tuple[bytes, Optional[str]]:
    """
    Load and validate a .bin file.
    
    Returns (contents, action_message).
    - If action_message is not None, it describes what was done (e.g., "Trimmed from 572 bytes")
    - If action_message is None, the file was valid at exactly 540 bytes
    
    Raises FileValidationError if the file cannot be used.
    """
    try:
        contents = path.read_bytes()
    except Exception as e:
        raise FileValidationError(f"Could not read file: {e}")
    
    size = len(contents)
    
    if size < NTAG215_BYTES:
        raise FileValidationError(
            f"Input is {size} bytes but a full NTAG215 dump must be "
            f"exactly {NTAG215_BYTES} bytes ({NTAG215_PAGES} pages × 4 bytes)"
        )
    
    if size > NTAG215_BYTES:
        if trim_oversize:
            action = f"Trimmed from {size} bytes to {NTAG215_BYTES} bytes"
            return contents[:NTAG215_BYTES], action
        elif log_oversize_only:
            raise FileValidationError(
                f"Input is {size} bytes (oversize). A full NTAG215 dump must be "
                f"exactly {NTAG215_BYTES} bytes. Skipping."
            )
        else:
            raise FileValidationError(
                f"Input is {size} bytes but a full NTAG215 dump must be "
                f"exactly {NTAG215_BYTES} bytes"
            )
    
    return contents, None


def convert(contents: bytes) -> Tuple[str, int]:
    """
    Convert all 135 pages from a 540-byte amiibo dump into Flipper page format.
    
    Each page is 4 bytes formatted as uppercase hex separated by spaces:
        Page 0: DE AD BE EF
    
    The input must already be exactly 540 bytes (validated beforehand).
    
    :param contents: byte array from a .bin file — must be exactly 540 bytes
    :return: Tuple of (full page string, page count = 135)
    """
    buffer = []
    for page_num in range(NTAG215_PAGES):
        offset = page_num * 4
        hex_bytes = " ".join(f"{b:02X}" for b in contents[offset : offset + 4])
        buffer.append(f"Page {page_num}: {hex_bytes}")
    return "\n".join(buffer), NTAG215_PAGES


def get_uid_bytes(contents: bytes) -> list[int]:
    """
    Return the 7-byte UID assembled from the source dump.
    The NTAG215 UID layout skips the BCC0 check byte at position 3,
    so bytes 0-2 and 4-7 form the 7-byte UID.
    :param contents: The bytes object we're operating on
    :return: UID bytes as integers
    """
    if len(contents) < 8:
        raise ValueError("Input is too short to contain amiibo UID bytes")

    return [
        contents[0],
        contents[1],
        contents[2],
        contents[4],
        contents[5],
        contents[6],
        contents[7],
    ]


def get_amiibo_pwd(uid: list[int]) -> str:
    """
    Compute the amiibo password bytes from the 7-byte UID.
    The PWD algorithm is specific to the Nintendo amiibo locking scheme.
    :param uid: UID bytes as integers (7 bytes, from get_uid_bytes)
    :return: Password bytes formatted as uppercase hex separated by spaces
    """
    pwd = [
        0xAA ^ uid[1] ^ uid[3],
        0x55 ^ uid[2] ^ uid[4],
        0xAA ^ uid[3] ^ uid[5],
        0x55 ^ uid[4] ^ uid[6],
    ]
    return " ".join(f"{byte:02X}" for byte in pwd)


def get_uid(contents: bytes) -> str:
    """
    Return the 7-byte UID as a space-separated uppercase hex string.
    The UID is bytes 0-2 then 4-7 (byte 3 is BCC0 and is skipped).
    :param contents: The bytes object we're operating on
    :return: something like `04 DE AD BE EF 12 34`
    """
    return " ".join(f"{byte:02X}" for byte in get_uid_bytes(contents))


def parse_signature_hex(raw: str) -> str:
    """
    Parse and validate a --signature-hex argument.
    Accepts either:
      - 64 continuous hex chars (no spaces): AABBCCDD...
      - 32 space-separated byte pairs:       AA BB CC DD ...
    Returns the canonical Flipper format: space-separated uppercase hex bytes.
    Raises ValueError if the input does not represent exactly 32 bytes.
    """
    stripped = raw.replace(" ", "").replace("\t", "")
    if len(stripped) == 64:
        try:
            data = bytes.fromhex(stripped)
        except ValueError:
            raise ValueError(f"--signature-hex contains invalid hex characters: {raw!r}")
    else:
        parts = raw.split()
        if len(parts) != 32:
            raise ValueError(
                f"--signature-hex must be exactly 32 bytes "
                f"(got {len(parts)} token(s) from: {raw!r})"
            )
        try:
            data = bytes(int(p, 16) for p in parts)
        except ValueError:
            raise ValueError(f"--signature-hex contains invalid hex bytes: {raw!r}")

    if len(data) != 32:
        raise ValueError(f"--signature-hex must be exactly 32 bytes, got {len(data)}")

    return " ".join(f"{b:02X}" for b in data)


def validate_config_pages(pages: list[str]) -> None:
    """
    Warn if pages 130-132 don't match the expected NTAG215 config/lock values
    for a standard amiibo. These pages are set by the factory and should not
    be modified; deviations may indicate a corrupted or non-standard dump.
    :param pages: List of page strings in "Page N: XX YY ZZ WW" format
    """
    checks = {
        130: EXPECTED_PAGE_130,
        131: EXPECTED_PAGE_131,
        132: EXPECTED_PAGE_132,
    }
    for page_num, expected in checks.items():
        actual = pages[page_num].split(": ", 1)[1]
        if actual != expected:
            logging.warning(
                f"Page {page_num} is '{actual}' but expected '{expected}' "
                "for a standard NTAG215 amiibo. Dump may be corrupt or non-standard."
            )


def assemble_code(contents: bytes, signature: Optional[str] = None) -> str:
    """
    Convert a 540-byte amiibo .bin dump to a Flipper-compatible .nfc file string.

    Pages 133 and 134 are intentionally overwritten after conversion:
      - Page 133 (PWD):  The NTAG215 password used for authenticated writes.
        Raw amiibo dumps store the real PWD here; Flipper emulation requires
        the correct computed value to respond properly to PWD_AUTH commands
        from amiibo-aware reader/writer devices.
      - Page 134 (PACK): The password acknowledgement. The amiibo PACK is
        always 80 80, followed by two zero padding bytes.

    :param contents: File contents from a 540-byte amiibo .bin file
                    (validation must have been done beforehand)
    :param signature: Optional pre-validated 32-byte originality signature string
    :return: A string to be written to an .nfc file
    """
    conversion, page_count = convert(contents)
    pages = conversion.splitlines()

    uid_bytes = get_uid_bytes(contents)
    pwd = get_amiibo_pwd(uid_bytes)
    pack = "80 80 00 00"

    # Warn on unexpected config/lock pages before patching
    validate_config_pages(pages)

    # Overwrite pages 133 and 134 with computed authentication values.
    # These MUST be correct for the Flipper to respond properly to PWD_AUTH
    # commands issued by amiibo-aware devices (e.g. Nintendo Switch, amiibo readers).
    pages[133] = f"Page 133: {pwd}"
    pages[134] = f"Page 134: {pack}"
    conversion = "\n".join(pages)

    # Raw amiibo .bin dumps do NOT contain the NTAG215 originality signature —
    # that value lives in a separate NFC memory area and is not exported by most
    # dump tools. A zeroed signature is used as a safe placeholder; some strict
    # Flipper firmware versions or reader devices may reject emulation without
    # the real NTAG originality signature.
    if signature is None:
        logging.warning(
            "No --signature-hex supplied. Outputting a zeroed Signature field. "
            "Some Flipper firmware versions or reader devices may reject the "
            "emulation without the real NTAG originality signature."
        )
        signature = ZEROED_SIGNATURE

    return f"""Filetype: Flipper NFC device
Version: 2
# Nfc device type can be UID, Mifare Ultralight, Bank card
Device type: NTAG215
# UID, ATQA and SAK are common for all formats
UID: {get_uid(contents)}
ATQA: 44 00
SAK: 00
Data format version: 1
# Mifare Ultralight specific data
# Note: raw amiibo .bin dumps do not include the NTAG originality signature.
# The value below is zeroed unless --signature-hex was supplied.
Signature: {signature}
Mifare version: 00 04 04 02 01 00 11 03
Counter 0: 0
Tearing 0: 00
Counter 1: 0
Tearing 1: 00
Counter 2: 0
Tearing 2: 00
Pages total: {page_count}
Pages read: {page_count}
{conversion}
"""


def iter_bin_files(input_dir: Path) -> Iterator[Path]:
    """
    Recursively iterate over all .bin files in a directory tree.
    Case-insensitive matching.
    """
    for item in input_dir.rglob("*"):
        if item.is_file() and item.suffix.lower() == ".bin":
            yield item


def resolve_single_file_output(input_file: Path, output_arg: Path) -> Path:
    """
    Resolve the output path for a single-file conversion.
    
    If output_arg is a .nfc file path, return it directly.
    If output_arg is a directory, return output_arg / input_file_base.nfc
    """
    output_arg = Path(output_arg)
    if output_arg.suffix.lower() == ".nfc":
        return output_arg
    else:
        return output_arg / f"{input_file.stem}.nfc"


def resolve_directory_output(
    input_root: Path, input_file: Path, output_root: Path
) -> Path:
    """
    Resolve the output path for a file in directory mode.
    
    Mirrors the relative folder structure from input_root.
    Example:
      input_root:  /home/user/Amiibo Bin
      input_file:  /home/user/Amiibo Bin/Zelda/Link.bin
      output_root: /tmp/AmiiboOut
      -> /tmp/AmiiboOut/Zelda/Link.nfc
    """
    relative = input_file.relative_to(input_root)
    return output_root / relative.parent / f"{relative.stem}.nfc"


def convert_single_file(
    input_file: Path,
    output_file: Path,
    signature: Optional[str] = None,
    trim_oversize: bool = False,
    log_oversize_only: bool = False
) -> ConversionResult:
    """
    Convert a single .bin file to .nfc format.
    
    Returns a ConversionResult describing what happened.
    Raises no exceptions; all errors are captured in the result.
    """
    try:
        contents, action_msg = load_bin_file(
            input_file,
            trim_oversize=trim_oversize,
            log_oversize_only=log_oversize_only
        )
        
        input_size = len(contents)
        nfc_content = assemble_code(contents, signature)
        write_output(input_file.stem, nfc_content, output_file)
        
        action = "trimmed" if action_msg else "success"
        return ConversionResult(
            action=action,
            input_path=input_file,
            input_size=input_size,
            output_path=output_file,
            message=action_msg
        )
    
    except FileValidationError as e:
        input_size = 0
        try:
            input_size = input_file.stat().st_size
        except:
            pass
        
        # Determine skip reason from error message
        msg_str = str(e)
        if "undersize" in msg_str.lower() or "short" in msg_str.lower():
            action = "skipped_undersize"
        elif "oversize" in msg_str.lower():
            action = "skipped_oversize"
        else:
            action = "skipped_error"
        
        return ConversionResult(
            action=action,
            input_path=input_file,
            input_size=input_size,
            output_path=None,
            message=msg_str
        )
    
    except Exception as e:
        input_size = 0
        try:
            input_size = input_file.stat().st_size
        except:
            pass
        
        return ConversionResult(
            action="skipped_error",
            input_path=input_file,
            input_size=input_size,
            output_path=None,
            message=str(e)
        )


def process_single_file(
    input_file: Path,
    output_arg: Path,
    signature: Optional[str] = None,
    trim_oversize: bool = False,
    log_oversize_only: bool = False
) -> int:
    """
    Process a single .bin file in single-file mode.
    
    Returns:
    - 0 on success
    - nonzero on failure
    """
    output_file = resolve_single_file_output(input_file, output_arg)
    
    result = convert_single_file(
        input_file,
        output_file,
        signature=signature,
        trim_oversize=trim_oversize,
        log_oversize_only=log_oversize_only
    )
    
    if result.action == "success":
        logging.info(f"Converted: {result.output_path}")
        return 0
    elif result.action == "trimmed":
        logging.warning(f"{result.input_path.name}: {result.message}")
        logging.info(f"Converted: {result.output_path}")
        return 0
    else:
        logging.error(f"Failed to convert {result.input_path}: {result.message}")
        return 1


def process_directory(
    input_dir: Path,
    output_dir: Path,
    signature: Optional[str] = None,
    trim_oversize: bool = False,
    log_oversize_only: bool = False
) -> int:
    """
    Process all .bin files in a directory tree in directory mode.
    
    Returns:
    - 0 if at least one file was converted successfully
    - nonzero otherwise
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    results: list[ConversionResult] = []
    
    for bin_file in iter_bin_files(input_dir):
        output_file = resolve_directory_output(input_dir, bin_file, output_dir)
        result = convert_single_file(
            bin_file,
            output_file,
            signature=signature,
            trim_oversize=trim_oversize,
            log_oversize_only=log_oversize_only
        )
        results.append(result)
        
        if result.action == "success":
            logging.info(f"Converted: {result.output_path}")
        elif result.action == "trimmed":
            logging.warning(f"{result.input_path}: {result.message}")
            logging.info(f"Converted: {result.output_path}")
        elif result.action == "skipped_oversize":
            logging.warning(f"Skipped oversize: {result.input_path} ({result.input_size} bytes)")
        elif result.action == "skipped_undersize":
            logging.warning(f"Skipped undersize: {result.input_path} ({result.input_size} bytes)")
        else:
            logging.warning(f"Skipped: {result.input_path} - {result.message}")
    
    # Print summary
    print_summary(results)
    
    # Return success if at least one file was converted
    has_success = any(r.action in ("success", "trimmed") for r in results)
    return 0 if has_success else 1


def print_summary(results: list[ConversionResult]) -> None:
    """
    Print a summary of directory mode conversion results.
    """
    total = len(results)
    successful = len([r for r in results if r.action == "success"])
    trimmed = len([r for r in results if r.action == "trimmed"])
    oversize = len([r for r in results if r.action == "skipped_oversize"])
    undersize = len([r for r in results if r.action == "skipped_undersize"])
    errors = len([r for r in results if r.action == "skipped_error"])
    
    print("\n" + "=" * 70)
    print("CONVERSION SUMMARY")
    print("=" * 70)
    print(f"Total .bin files found:      {total}")
    print(f"Converted successfully:     {successful}")
    if trimmed > 0:
        print(f"Trimmed and converted:      {trimmed}")
    if oversize > 0:
        print(f"Skipped (oversize):         {oversize}")
    if undersize > 0:
        print(f"Skipped (undersize):        {undersize}")
    if errors > 0:
        print(f"Skipped (other errors):     {errors}")
    print("=" * 70 + "\n")


def get_args():
    parser = argparse.ArgumentParser(
        prog="amiibo.py",
        description="Convert amiibo .bin dumps to Flipper .nfc files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Single file:
    python3 amiibo.py --file ./Wario.bin -o ./Wario.nfc
    python3 amiibo.py --file ./Wario.bin -o ./output/
  
  Directory:
    python3 amiibo.py --dir "./Amiibo Bin" -o ./AmiiboOut
        """
    )
    
    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--file",
        type=Path,
        metavar="PATH",
        help="Convert a single .bin file"
    )
    mode_group.add_argument(
        "--dir",
        type=Path,
        metavar="PATH",
        help="Convert all .bin files in a directory (recursively)"
    )
    
    # Output path
    parser.add_argument(
        "-o", "--output",
        required=True,
        type=Path,
        metavar="PATH",
        help="Output .nfc file or directory"
    )
    
    # Oversize handling (mutually exclusive)
    oversize_group = parser.add_mutually_exclusive_group()
    oversize_group.add_argument(
        "--trim-oversize",
        action="store_true",
        help="If a file is >540 bytes, trim to 540 bytes and continue (with warning)"
    )
    oversize_group.add_argument(
        "--log-oversize-only",
        action="store_true",
        help="If a file is >540 bytes, log it and skip it (with warning)"
    )
    
    # Signature
    parser.add_argument(
        "--signature-hex",
        type=str,
        metavar="HEX",
        help=(
            "The 32-byte NTAG215 originality signature as 64 continuous hex chars "
            "(e.g. AABBCCDD...) or 32 space-separated byte pairs (e.g. 'AA BB CC ...')"
        )
    )
    
    # Verbosity
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Show extra info: -v for info level, -vv for debug level"
    )
    
    args = parser.parse_args()
    
    # Set up logging
    if args.verbose >= 2:
        log_level = logging.DEBUG
    elif args.verbose >= 1:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING
    
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")
    
    return args


def main() -> int:
    """
    Main entry point. Returns 0 on success, nonzero on failure.
    """
    args = get_args()
    
    # Parse and validate signature if provided
    signature: Optional[str] = None
    if args.signature_hex:
        try:
            signature = parse_signature_hex(args.signature_hex)
        except ValueError as e:
            logging.error(f"Invalid --signature-hex: {e}")
            return 1
    
    try:
        if args.file:
            # Single-file mode
            input_file = Path(args.file).resolve()
            if not input_file.exists():
                logging.error(f"Input file not found: {input_file}")
                return 1
            if not input_file.is_file():
                logging.error(f"Input path is not a file: {input_file}")
                return 1
            
            output_path = Path(args.output).resolve()
            
            return process_single_file(
                input_file,
                output_path,
                signature=signature,
                trim_oversize=args.trim_oversize,
                log_oversize_only=args.log_oversize_only
            )
        
        else:  # args.dir
            # Directory mode
            input_dir = Path(args.dir).resolve()
            if not input_dir.exists():
                logging.error(f"Input directory not found: {input_dir}")
                return 1
            if not input_dir.is_dir():
                logging.error(f"Input path is not a directory: {input_dir}")
                return 1
            
            output_dir = Path(args.output).resolve()
            
            return process_directory(
                input_dir,
                output_dir,
                signature=signature,
                trim_oversize=args.trim_oversize,
                log_oversize_only=args.log_oversize_only
            )
    
    except KeyboardInterrupt:
        logging.error("Interrupted by user")
        return 130
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        if args.verbose >= 2:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
