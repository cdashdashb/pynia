import sys

import pyglet

import nia as NIA

# How often to refresh the display, in milliseconds. Acquisition now runs
# continuously in the background, so this only controls the render cadence.
UPDATE_INTERVAL_MS = 50

# global scope stuff
backgound = pyglet.image.load('static/images/pynia.png')
step = pyglet.image.load('static/images/step.png')
nia = None
acquisition = None
nia_data = None
window = None


def update(dt):
    """Render one frame from the most recently acquired data.

    The background acquisition thread keeps the rolling sample window full;
    here we just fold in the new samples (pump) and draw. No per-frame thread
    start/join, and no shared array mutated across threads.
    """
    window.clear()

    # bail out if the acquisition thread has died (e.g. access denied / unplug)
    if nia_data.AccessDeniedError:
        pyglet.app.exit()
        return

    # fold newly acquired samples into the rolling window
    nia_data.pump()

    # fill in the background image
    backgound.blit(0, 0)

    # get the fourier data from the NIA
    data, steps = nia_data.fourier()

    # render an Intensity-based graph of the data
    image = pyglet.image.ImageData(160, 140, 'I', data)
    image.blit(20, 20)

    # render step scales of the 'brain-fingers'
    for i in range(6):  # this blits the brain-fingers blocks
        for j in range(int(steps[i])):
            step.blit(i * 50 + 100, j * 15 + 200)

    # get a waveform of the last 1 second of data
    data = nia_data.waveform()

    # render an RGB graph of the waveform data
    image = pyglet.image.ImageData(410, 140, 'RGB', data)
    image.blit(210, 20)


if __name__ == "__main__":
    # open the NIA, or exit with a failure code
    nia = NIA.NIA()
    if not nia.open():
        sys.exit(1)

    # start the background acquisition (producer) thread
    acquisition = NIA.NiaAcquisition(nia)
    acquisition.start()

    # the consumer/processor that the render loop reads from
    nia_data = NIA.NiaData(nia, acquisition)

    # open a window and schedule periodic updates
    window = pyglet.window.Window(caption="pyNIA")
    pyglet.clock.schedule_interval(update, UPDATE_INTERVAL_MS / 1000.0)
    pyglet.app.run()

    # when pyglet exits, stop acquisition, close out the NIA and exit
    acquisition.stop()
    acquisition.join(timeout=1.0)
    nia.close()
    sys.exit(1 if nia_data.AccessDeniedError else 0)
