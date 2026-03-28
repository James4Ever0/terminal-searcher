# if socket is known, how to attach to tmux session?
tmux -L <socket_name> attach-session -t <session_name>
# or for gnu screen
screen -r /path/to/socketdir/<sessionname>