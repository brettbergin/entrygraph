def go(w):
    # receiver `w` is an unknown local -> fuzzy-binds to Worker.process
    # (unique name), producing a CROSS-FILE FUZZY edge into worker.py
    return w.process()
