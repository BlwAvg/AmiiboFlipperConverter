# AmiiboFlipperConverter

Converts amiibo `.bin` dumps to Flipper-compatible `.nfc` files.

Convert a single file:

`python3 amiiboconvert.py --file [path-to-file].bin -o [output].nfc`

You can also pass an output folder for single-file mode:

`python3 amiiboconvert.py --file [path-to-file].bin -o [output-folder]`

Convert multiple files in a directory (recursive):

`python3 amiiboconvert.py --dir [input-folder] -o [output-folder]`

If some dumps are larger than 540 bytes and you want to trim them automatically:

`python3 amiiboconvert.py --dir [input-folder] -o [output-folder] --trim-oversize`

If you only want to log and skip oversize dumps:

`python3 amiiboconvert.py --dir [input-folder] -o [output-folder] --log-oversize-only`

To provide an NTAG originality signature:

`python3 amiiboconvert.py --file [path-to-file].bin -o [output].nfc --signature-hex [64-hex-chars-or-32-bytes]`

To display help, run:

`python3 amiiboconvert.py -h`

If you run into problems, check the warnings/errors in output and re-run with `-v` (or `-vv`) for more detail.
