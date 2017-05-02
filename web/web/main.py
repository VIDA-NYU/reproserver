from flask import Flask
import pika


app = Flask(__name__)

count = 0


@app.route('/')
def hello():
    global count
    count += 1
    app.logger.info("count=%d", count)
    return 'Hello World! I have been seen {} times.\n'.format(count)


def main():
    app.logger.info("web running")
    app.run(host="0.0.0.0", port=8000, debug=True)
