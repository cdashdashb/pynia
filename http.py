import json
import sys
import time

import web

import nia as NIA

# How often the background loop recomputes the brain-finger values (seconds).
UPDATE_INTERVAL_S = 0.05

urls = (
    '/', 'index',
    '/get_steps', 'get_steps'
)

# global scope stuff
nia = None
acquisition = None
nia_data = None


class index:
    def GET(self):
        render = web.template.render("templates/")
        return render.index()


class get_steps:
    def GET(self):
        web.header("Content-Type", "application/json")
        data = {
            # default to zeros until the first update has run
            "brain_fingers": getattr(web, "brain_fingers", [0] * 6)
        }
        return json.dumps(data)


class Updater:
    """Background consumer loop: pump samples and publish brain-finger values."""

    def __init__(self, nia_data, acquisition):
        self.nia_data = nia_data
        self.acquisition = acquisition

    def update(self):
        while True:
            # stop if the acquisition thread has died (e.g. access denied)
            if self.nia_data.AccessDeniedError:
                return

            # fold newly acquired samples into the rolling window
            self.nia_data.pump()

            # compute and publish the fourier-derived brain-finger values
            _, steps = self.nia_data.fourier()
            web.brain_fingers = steps

            # throttle to the configured refresh rate
            time.sleep(UPDATE_INTERVAL_S)


if __name__ == "__main__":
    app = web.application(urls, globals())
    web.brain_fingers = [0] * 6

    # open the NIA, or exit with a failure code
    nia = NIA.NIA()
    if not nia.open():
        sys.exit(1)

    # start the background acquisition (producer) thread
    acquisition = NIA.NiaAcquisition(nia)
    acquisition.start()

    # the consumer/processor that the updater reads from
    nia_data = NIA.NiaData(nia, acquisition)

    # kick off the background updater loop (daemon so it dies with the process)
    import threading
    updater = Updater(nia_data, acquisition)
    update_thread = threading.Thread(target=updater.update, daemon=True)
    update_thread.start()

    # run the app
    app.run()

    # when web.py exits, stop acquisition, close out the NIA and exit gracefully
    acquisition.stop()
    acquisition.join(timeout=1.0)
    nia.close()
    sys.exit(0)
