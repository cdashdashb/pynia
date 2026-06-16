# pynia

- - -
#### This is a fork of David Ng's (discontinued?) pynia project on Google Code: http://code.google.com/p/pynia/
- - -

# Getting started
## Requirements
* **Python 3.10+**
* [NumPy](https://numpy.org/) (1.26+)
* [PyUSB](https://github.com/pyusb/pyusb) **1.x** (uses the `usb.core` / `usb.util` API)
* [pyglet](https://pyglet.org/) 2.x — for the pyglet GUI (`pynia.py`)
* [libusb](https://libusb.info/) **1.0** at the system level
* *(optional)* [SciPy](https://scipy.org/) — only for the higher-quality DSP path
* *(optional)* [web.py](https://webpy.org/) — only for the HTTP front-end (`http.py`)

### Install
```sh
pip install -r requirements.txt        # numpy, pyusb, pyglet
# optional extras:
pip install scipy        # higher-quality DSP (see "DSP options" below)
pip install web.py       # only if you want the http.py front-end
```

libusb itself:
* **Windows** — a `libusb-1.0.dll` is bundled in this repo and loaded automatically; nothing else to install.
* **GNU/Linux** — install `libusb-1.0-0` from your package manager (e.g. `sudo apt install libusb-1.0-0`). Also see the [Access Denied troubleshooting section](#access-denied-warning-on-gnulinux).
* **macOS** — `brew install libusb`.

> Note: `web.py` is largely unmaintained. `http.py` still uses it for now; a future pass should migrate it to Flask/FastAPI.

## Finding your NIA's USB IDs
The NIA enumerates as **`1234:0000`** ("Neural Impulse Actuator Prototype 1.0"). These IDs look like placeholders but are genuinely what the hardware reports, so the defaults usually just work. If your unit differs, find the real IDs:
* **Linux** — `lsusb` and look for *Brain Actuated Technologies* / `1234:0000`.
* **Windows** — Device Manager → the NIA device → Properties → Details → *Hardware Ids* (`USB\VID_1234&PID_0000`).

Override the defaults without editing code via environment variables (decimal or `0x` hex):
```sh
NIA_VENDOR_ID=0x1234 NIA_PRODUCT_ID=0x0000 python pynia.py
```
or programmatically: `NIA.NIA(vendor_id=0x1234, product_id=0x0000)`.

## DSP options
The default DSP exactly reproduces the original visuals (brick-wall FFT filter and plain decimation). A cleaner, anti-aliased path (anti-aliased decimation + zero-phase Butterworth low-pass) is available behind a flag and requires SciPy:
```python
nia_data = NIA.NiaData(nia, acquisition, high_quality_dsp=True)
```

## Usage
There are two user interfaces for pyNIA: pyglet and HTML5.
### pyglet
![pyglet](/screenshots/pynia-pyglet.png)

The pyglet interface has a histogram of the 6 BrainFingers at top, a spectograph that I don't understand in the bottom left, and a general compiled waveform of the raw output from the device in the bottom right.

#### running it
I never got pyglet working on Mac, but it works fine on GNU/Linux. Simply run `python pynia.py`.
### HTML5
![html5](/screenshots/pynia-http.png)

[Click here for the annotated version](/screenshots/pynia-http-annotated.png). The HTML5 interface has the same histogram as the pyglet version at top, but the other two images are different. The bottom left has a distorted hexagon I have nicknamed the Brain Shape, where the distortion to each vertex is determined by a specific BrainFinger. The hexagon is bluer when all frequencies are low, redder when beta frequencies are high, and greener when alpha frequencies are high. The bottom right is a historical graph of all six BrainFinger values, instead of a single compiled waveform.

#### running it
1. You will need to install CoffeeScript, have `coffee` on your path, and run `batch/coffee-compile.sh`. Alternatively, you can use [Coffee2JS](http://js2coffee.org/#coffee2js) to convert `/src/coffee/index.coffee` and save the output to `static/js/index.js`.
2. Once you have compiled the CoffeeScript code, run `python http.py` and visit `http://localhost:8080`.

# Troubleshooting
#### Versions
I ran into quite a few problems getting **pynia** working. David Ng's project
had 2 fairly different versions of the code. I'm going to be developing
primarily off the version **0.0.1** codebase because it required less
modifications to make it functional, and it also has much better documentation.
#### "Access Denied" warning on GNU/Linux
By default, libusb has no read/write access to USB devices, so claiming the NIA
fails with an *Access Denied* / `USBError`. To use **pynia** without root, add a
udev rule for the NIA. A ready-to-use rule is in the `udev/` folder of this repo:

```sh
sudo cp udev/47-ocz-nia.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then unplug the NIA and plug it back in.

The shipped rule matches `idVendor==1234`, `idProduct==0000` and grants access
via `TAG+="uaccess"` (works on modern systemd systems for the logged-in user),
with a `MODE="0660"`/`GROUP="plugdev"` fallback — change the group to one your
user belongs to (run `groups` to check) if `uaccess` isn't available.

On Linux the kernel may bind its generic HID driver to the NIA first; the code
detaches it automatically (`detach_kernel_driver`) before claiming the
interface, so no manual unbinding is needed.

# License #
#### [MIT License](http://opensource.org/licenses/mit-license.php)

# Credits
The original documentation of the project follows:

>#### This is a simple GUI to play with the NIA on Mac and Linux until OCZ release a set of drivers for these platforms.
>
>It requires the following dependencies:
>* http://code.google.com/p/pyglet/
>* http://libusb.wiki.sourceforge.net/
>* http://sourceforge.net/projects/pyusb/
>* http://sourceforge.net/projects/numpy/
