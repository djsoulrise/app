"""Refresh the embedded browser on its owning GUI thread after restoring."""
def refresh(window):
    form = window.native
    if form is None:
        return
    from System import Action

    def repaint():
        form.PerformLayout()
        for control in form.Controls:
            core = getattr(control, 'CoreWebView2', None)
            if core is not None:
                # Text scaling is handled by Ctrl+wheel in the app, not browser page zoom.
                core.Settings.IsZoomControlEnabled = False
            control.Invalidate(True)
            control.Refresh()
        form.Invalidate(True)
        form.Update()

    if form.InvokeRequired:
        form.Invoke(Action(repaint))
    else:
        repaint()
