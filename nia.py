"""Low-level I/O and DSP for the OCZ Neural Impulse Actuator (NIA).

This module talks to the NIA over USB (PyUSB 1.x / libusb), decodes its
24-bit biosignal samples, and turns them into the "brain finger" frequency-band
energies and waveform images that the front-ends (``pynia.py``, ``http.py``)
render.

The genuinely reusable / spec-defining part of this file is the USB protocol
decoding in :func:`decode_packet` and the constants just below -- those describe
how to talk to the hardware. The DSP/rendering code is visualization-grade: the
"brain finger" bands are loosely-defined energy buckets, not calibrated EEG
measurements, so the comments below avoid overstating them.
"""

import collections
import math
import os
import sys
import threading

import numpy as np
from numpy import fft

import usb.core
import usb.util
import usb.backend.libusb1


# ---------------------------------------------------------------------------
# USB device identity
# ---------------------------------------------------------------------------
# The NIA genuinely enumerates as 1234:0000 ("Neural Impulse Actuator
# Prototype 1.0"). These look like placeholder IDs but they are the real ones
# the hardware reports -- confirmed by USB ID databases and by the udev rule
# shipped in this repo. They can still be overridden (constructor args take
# precedence, then the NIA_VENDOR_ID / NIA_PRODUCT_ID environment variables) in
# case a particular unit reports something different.
#
# To find the real IDs for your unit:
#   * Linux:   `lsusb`  -> look for "Brain Actuated Technologies" / 1234:0000
#   * Windows: Device Manager -> NIA -> Properties -> Details ->
#              "Hardware Ids"  (USB\VID_1234&PID_0000)
NIA_VENDOR_ID = 0x1234
NIA_PRODUCT_ID = 0x0000
NIA_INTERFACE_ID = 0

# The NIA streams samples on a 64-byte *interrupt* IN endpoint. The original
# code carried BULK_IN_EP=0x83 / BULK_OUT_EP=0x02 constants and a method called
# `bulk_read`, but the device is read with an interrupt transfer on 0x81 -- the
# bulk constants were never used and were misleading, so they have been removed
# and the read path is named accordingly (see NIA.interrupt_read).
NIA_INTERRUPT_IN_EP = 0x81
PACKET_LENGTH = 0x40            # 64 bytes per interrupt-IN packet
READ_TIMEOUT_MS = 25           # per-read USB timeout

# --- 64-byte packet layout (interrupt IN on 0x81) --------------------------
# bytes [0 .. 3*count-1]  : up to 16 samples, packed from the start of the
#                           packet, 3 bytes each, unsigned 24-bit LITTLE-ENDIAN
# byte  [SAMPLE_COUNT_OFFSET=54] : number of valid samples in this packet (0..16)
# (remaining bytes are status/padding we don't decode)
SAMPLE_COUNT_OFFSET = 54
BYTES_PER_SAMPLE = 3
MAX_SAMPLES_PER_PACKET = 16


def decode_packet(packet):
    """Decode one 64-byte NIA interrupt-IN packet into 24-bit LE samples.

    ``packet[SAMPLE_COUNT_OFFSET]`` holds the number of valid samples; each
    sample is 3 consecutive bytes, little-endian, unsigned:

        value = b0 | (b1 << 8) | (b2 << 16)

    This is the exact same arithmetic as the original
    ``data[i*3+2]*65536 + data[i*3+1]*256 + data[i*3]`` -- just spelled as a
    little-endian decode. Returns a ``numpy.uint32`` array (possibly empty).
    """
    count = int(packet[SAMPLE_COUNT_OFFSET])
    # Guard against a malformed count byte so we never read past the packet.
    if count < 0 or count > MAX_SAMPLES_PER_PACKET:
        count = max(0, min(count, MAX_SAMPLES_PER_PACKET))
    samples = np.empty(count, dtype=np.uint32)
    for col in range(count):
        base = col * BYTES_PER_SAMPLE
        samples[col] = packet[base] | (packet[base + 1] << 8) | (packet[base + 2] << 16)
    return samples


def _bundled_backend():
    """Return a libusb backend using the DLL bundled next to this file.

    On Windows PyUSB needs an explicit libusb backend; this repo ships
    ``libusb-1.0.dll`` alongside the source. On Linux/Mac libusb is found on the
    system path, so we fall back to PyUSB's default discovery (``None``).
    """
    dll = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libusb-1.0.dll")
    if os.path.exists(dll):
        return usb.backend.libusb1.get_backend(find_library=lambda _: dll)
    return None  # PyUSB will locate libusb on its own


def _env_id(name, default):
    """Read an integer USB id from the environment (accepts decimal or 0x hex)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw, 0)


def _is_timeout(err):
    """True if a USBError is just a read timeout (normal when no data is ready)."""
    if getattr(err, "errno", None) == 110:  # ETIMEDOUT on Linux
        return True
    msg = str(err).lower()
    return "timeout" in msg or "timed out" in msg


class NIA:
    """Attaches the NIA device and provides low-level data collection."""

    def __init__(self, vendor_id=None, product_id=None, interface_id=NIA_INTERFACE_ID):
        self.vendor_id = vendor_id if vendor_id is not None else _env_id("NIA_VENDOR_ID", NIA_VENDOR_ID)
        self.product_id = product_id if product_id is not None else _env_id("NIA_PRODUCT_ID", NIA_PRODUCT_ID)
        self.interface_id = interface_id
        self._backend = _bundled_backend()
        # The usb.core.Device, populated by open(). None means "not connected".
        self.device = None

    def open(self):
        """Find, configure and claim the NIA. Returns True on success."""
        self.device = usb.core.find(
            idVendor=self.vendor_id,
            idProduct=self.product_id,
            backend=self._backend,
        )
        if self.device is None:
            print(
                "Failed to open NIA device (VID=0x%04x PID=0x%04x). Is it plugged in?"
                % (self.vendor_id, self.product_id),
                file=sys.stderr,
            )
            return False

        try:
            # On Linux the kernel HID driver usually grabs the device first;
            # detach it so we can claim the interface. NotImplementedError is
            # raised on platforms (Windows) where this concept doesn't apply.
            try:
                if self.device.is_kernel_driver_active(self.interface_id):
                    self.device.detach_kernel_driver(self.interface_id)
            except (NotImplementedError, usb.core.USBError):
                pass

            self.device.set_configuration()
            usb.util.claim_interface(self.device, self.interface_id)
        except usb.core.USBError as err:
            print("Failed to claim NIA device: %s" % err, file=sys.stderr)
            print(
                "If you're on GNU/Linux, see the README 'Access Denied' "
                "troubleshooting section (udev rules / permissions).",
                file=sys.stderr,
            )
            self.device = None
            return False

        return True

    def close(self):
        """Release the interface and free USB resources."""
        if self.device is not None:
            try:
                usb.util.release_interface(self.device, self.interface_id)
            except Exception as err:  # best-effort cleanup
                print(err, file=sys.stderr)
            usb.util.dispose_resources(self.device)
        self.device = None

    def interrupt_read(self):
        """Read one (up to) 64-byte interrupt-IN packet from the NIA.

        Raises RuntimeError if the device was never opened, and lets
        ``usb.core.USBError`` (including benign timeouts) propagate to the
        caller so the acquisition loop can distinguish timeouts from real
        failures.
        """
        if self.device is None:
            raise RuntimeError("NIA device is not open; call open() first.")
        return self.device.read(NIA_INTERRUPT_IN_EP, PACKET_LENGTH, timeout=READ_TIMEOUT_MS)

    # Backwards-compatible alias for the old (mis-named) method.
    bulk_read = interrupt_read


class NiaAcquisition(threading.Thread):
    """Background producer: continuously reads packets and buffers samples.

    This replaces the old start()/join()-per-frame pattern. A single daemon
    thread owns the USB device and pushes decoded samples into a bounded,
    lock-protected ring buffer (``collections.deque(maxlen=...)``). The render
    side calls :meth:`drain` to pull the samples accumulated since the last
    frame -- there is no shared mutable array between threads, so no data race.

    (``queue.Queue`` would work too, but a ``deque(maxlen=N)`` gives us free
    bounded memory: if the consumer stalls, the oldest samples are dropped
    rather than growing without limit, which matches the "only the last second
    of data matters" intent.)
    """

    def __init__(self, nia, buffer_samples=8192):
        super().__init__(daemon=True)
        self.nia = nia
        self._buffer = collections.deque(maxlen=buffer_samples)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        # Set to the offending USBError if acquisition dies (e.g. access denied
        # or device unplugged). Consumers should poll this and shut down.
        self.error = None

    def run(self):
        while not self._stop.is_set():
            try:
                packet = self.nia.interrupt_read()
            except usb.core.USBError as err:
                if _is_timeout(err):
                    # No data within the timeout window -- normal, keep going.
                    continue
                self.error = err
                print("Failed to read from NIA device: %s" % err, file=sys.stderr)
                print(
                    "If you're on GNU/Linux, see the README 'Access Denied' "
                    "troubleshooting section.",
                    file=sys.stderr,
                )
                break
            except RuntimeError as err:
                self.error = err
                break

            samples = decode_packet(packet)
            if samples.size:
                with self._lock:
                    self._buffer.extend(int(s) for s in samples)

    def drain(self):
        """Atomically remove and return all buffered samples (as a list)."""
        with self._lock:
            items = list(self._buffer)
            self._buffer.clear()
        return items

    def stop(self):
        """Signal the thread to finish (it is a daemon, so this is best-effort)."""
        self._stop.set()


class NiaData:
    """Consumer/processor: maintains the rolling sample window and DSP outputs.

    Pull model: call :meth:`pump` once per frame to fold newly acquired samples
    into the rolling window, then call :meth:`fourier` / :meth:`waveform` to get
    the images to render. Because pump/fourier/waveform all run on the same
    (render) thread and the acquisition thread only touches its own deque, there
    is no synchronization needed here.
    """

    RING_SIZE = 4096  # ~1 second of samples kept for display/analysis

    # --- fourier() layout constants -------------------------------------
    # The FFT magnitude spectrum is trimmed to bins [FFT_BIN_LO:FFT_BIN_HI].
    # With ~RING_SIZE samples spanning ~1 second, the sample rate is ~RING_SIZE
    # Hz and the FFT length is RING_SIZE, so Hz-per-bin ~= Fs/N ~= 1 Hz. That
    # makes the trimmed range roughly 4-44 Hz. NOTE: the NIA's sample rate is
    # not precisely calibrated here, so treat these Hz figures as approximate.
    FFT_BIN_LO = 4
    FFT_BIN_HI = 44                 # 40 bins kept
    FOURIER_WIDTH = 160             # image width; 40 bins x FOURIER_BIN_WIDTH
    FOURIER_BIN_WIDTH = 4           # each kept bin is drawn 4 px wide
    FOURIER_HEIGHT = 140

    # "Brain finger" band edges, expressed as INDICES INTO THE TRIMMED spectrum
    # x (which starts at FFT bin FFT_BIN_LO). So band i covers trimmed indices
    # [WAVE_BANDS[i] : WAVE_BANDS[i+1]), i.e. FFT bins (edge + FFT_BIN_LO).
    # With ~1 Hz/bin that is roughly 10-34 Hz split into 6 buckets. These are
    # loose energy buckets ("alpha/beta"-ish), not calibrated EEG bands.
    WAVE_BANDS = (6, 9, 12, 15, 20, 25, 30)
    FINGER_COUNT = 6
    FINGER_SCALE = 100.0            # divisor that scales a band sum to step count

    # --- waveform() layout constants ------------------------------------
    DECIMATION = 8                  # take every 8th sample before the FFT
    FILTER_OVER = 30                # brick-wall low-pass: keep |bin| < 30
    WAVE_WIDTH = 410                # output image width (px)
    WAVE_HEIGHT = 140               # output image height (px)
    # Skip the first WAVE_X_OFFSET decimated points (filter-edge transient) so
    # the displayed window ends at the most recent sample: 102 + 410 = 512 =
    # RING_SIZE / DECIMATION.
    WAVE_X_OFFSET = 102
    WAVE_COLOR = (0, 204, 255)      # trace color (RGB)
    WAVE_BG = (0, 0, 51)            # background color (RGB)

    def __init__(self, nia, acquisition, high_quality_dsp=False):
        self.nia = nia
        self.acquisition = acquisition
        # When True, use anti-aliased decimation and a proper IIR low-pass
        # (requires scipy). Defaults to False so the visual output is identical
        # to the original brick-wall/naive-decimation behavior.
        self.high_quality_dsp = high_quality_dsp
        self.Processed_Data = np.ones(self.RING_SIZE, dtype=np.uint32)
        # uint8 (not int8): bin energies reach 255, which overflows int8 under
        # NumPy 2.x. The rendered bytes ('I' intensity / 0-255) are unchanged.
        self.Fourier_Data = np.zeros((self.FOURIER_HEIGHT, self.FOURIER_WIDTH), dtype=np.uint8)

    @property
    def AccessDeniedError(self):
        """Back-compat flag: True if the acquisition thread has failed."""
        return self.acquisition is not None and self.acquisition.error is not None

    def pump(self):
        """Fold any newly acquired samples into the rolling window."""
        new = self.acquisition.drain() if self.acquisition is not None else []
        if new:
            self.Processed_Data = np.append(
                self.Processed_Data, np.asarray(new, dtype=np.uint32)
            )[-self.RING_SIZE:]  # keep exactly RING_SIZE most-recent samples

    def waveform(self):
        """Return an RGB image (bytes) of the recent, low-pass-filtered waveform.

        Decimates the rolling window, low-pass filters it, normalizes to the
        image height, and plots one lit pixel per column.
        """
        if self.high_quality_dsp:
            signal = self._waveform_hq()
        else:
            # --- original behavior (preserved by default) ---
            # Decimate by DECIMATION with NO anti-aliasing filter first. This
            # aliases any energy above Fs/(2*DECIMATION) back into band; it is
            # kept for visual parity. See _waveform_hq() for a correct path.
            decimated = self.Processed_Data[::self.DECIMATION]
            spectrum = fft.fft(decimated)            # fftn on a 1-D array == fft
            # Brick-wall low-pass: zero every bin with |index| >= FILTER_OVER.
            # Zeroing bins then ifft causes Gibbs ringing near transients; this
            # is acceptable for a visualization but is not a clean filter.
            spectrum[self.FILTER_OVER:-self.FILTER_OVER] = 0
            # ifft is complex; the displayed waveform is its real part. Taking
            # .real explicitly avoids a ComplexWarning / undefined int(complex).
            signal = fft.ifft(spectrum).real

        # Normalize into [~0, WAVE_HEIGHT) with a little head/foot room.
        x_max = signal.max() * 1.1
        x_min = signal.min() * 0.9
        span = (x_max - x_min) or 1.0  # avoid divide-by-zero on a flat signal
        norm = self.WAVE_HEIGHT * (signal - x_min) / span

        wave = np.zeros((self.WAVE_HEIGHT, self.WAVE_WIDTH, 3), dtype=np.uint8)
        wave[:, :] = self.WAVE_BG
        for i in range(self.WAVE_WIDTH):
            value = norm[i + self.WAVE_X_OFFSET]
            # Discard NaNs (can occur while the headset is being adjusted).
            if math.isnan(value):
                continue
            row = int(value)
            if 0 <= row < self.WAVE_HEIGHT:  # guard against out-of-range rows
                wave[row, i, :] = self.WAVE_COLOR
        return wave.tobytes()

    def _waveform_hq(self):
        """Anti-aliased decimation + zero-phase low-pass (requires scipy)."""
        from scipy import signal as sps
        # decimate() applies an anti-aliasing filter before downsampling.
        decimated = sps.decimate(self.Processed_Data.astype(float), self.DECIMATION)
        # Zero-phase Butterworth low-pass instead of a brick wall (no ringing).
        # Cutoff mirrors FILTER_OVER bins of an N-point FFT: FILTER_OVER / (N/2).
        nyq_fraction = min(0.99, self.FILTER_OVER / (len(decimated) / 2.0))
        b, a = sps.butter(4, nyq_fraction)
        return sps.filtfilt(b, a, decimated)

    def fourier(self):
        """Compute the spectrum image and the 6 "brain finger" band energies.

        Performs a Hanning-windowed FFT over the whole rolling window, trims it
        to the display bins, scrolls it into the rolling spectrogram image, and
        sums the trimmed magnitudes into 6 loosely-defined energy buckets.
        Returns ``(image_bytes, fingers)``.
        """
        # Scroll the spectrogram up by one row.
        self.Fourier_Data[1:self.FOURIER_HEIGHT, :] = self.Fourier_Data[0:self.FOURIER_HEIGHT - 1, :]

        window = np.hanning(len(self.Processed_Data))
        magnitude = np.abs(fft.fft(self.Processed_Data * window))  # fftn==fft for 1-D
        x = magnitude[self.FFT_BIN_LO:self.FFT_BIN_HI]            # trim to display bins

        x_max = x.max()
        x_min = x.min()
        span = (x_max - x_min) or 1.0
        x = 255 * (x - x_min) / span                              # normalize to 0..255

        # Marker row(s) highlighting the single dominant bin.
        pointer = np.zeros((self.FOURIER_WIDTH), dtype=np.uint8)
        peak = int(np.argmax(x))
        pointer[peak * self.FOURIER_BIN_WIDTH: peak * self.FOURIER_BIN_WIDTH + self.FOURIER_BIN_WIDTH] = 255

        # Widen each bin to FOURIER_BIN_WIDTH px (40 bins x 4 = 160 = width).
        y = np.ravel(np.vstack((x, x, x, x)), "F")
        self.Fourier_Data[5, :] = y
        self.Fourier_Data[0:4, :] = np.vstack((pointer, pointer, pointer, pointer))

        fingers = []
        for i in range(self.FINGER_COUNT):
            band_sum = np.sum(x[self.WAVE_BANDS[i]:self.WAVE_BANDS[i + 1]]) / self.FINGER_SCALE
            # Discard NaNs (can occur while the headset is being adjusted).
            fingers.append(0 if math.isnan(band_sum) else band_sum)
        return self.Fourier_Data.tobytes(), fingers
