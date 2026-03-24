xterm.js Local Setup (as requested):
To download xterm.js locally instead of CDN, run:

```bash
mkdir -p flashback_terminal/static/js/vendor \
flashback_terminal/static/css/vendor

curl -L -o flashback_terminal/static/js/vendor/xterm.js \
https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js

curl -L -o flashback_terminal/static/css/vendor/xterm.css \
https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css

curl -L -o \
flashback_terminal/static/js/vendor/xterm-addon-fit.js \
https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js
```

Then update index.html paths to use /static/js/vendor/ and
/static/css/vendor/.


