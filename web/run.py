from flask import Flask
import sys


app = Flask(__name__)

count = 0

@app.route('/')
def hello():
    global count
    count += 1
    return 'Hello World! I have been seen {} times.\n'.format(count)

if __name__ == "__main__":
    print >>sys.stderr, "web running"
    app.run(host="0.0.0.0", port=8000, debug=True)
