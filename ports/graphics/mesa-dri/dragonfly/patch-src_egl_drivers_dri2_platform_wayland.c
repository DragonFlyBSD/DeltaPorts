diff --git a/src/egl/drivers/dri2/platform_wayland.c b/src/egl/drivers/dri2/platform_wayland.c
index c4177f8799c..0dbc7aa4803 100644
--- a/src/egl/drivers/dri2/platform_wayland.c
+++ b/src/egl/drivers/dri2/platform_wayland.c
@@ -1347,7 +1347,7 @@ registry_handle_global_drm(void *data, struct wl_registry *registry,
       dri2_dpy->wl_drm =
          wl_registry_bind(registry, name, &wl_drm_interface, MIN2(version, 2));
       wl_drm_add_listener(dri2_dpy->wl_drm, &drm_listener, dri2_dpy);
-   } else if (strcmp(interface, "zwp_linux_dmabuf_v1") == 0 && version >= 3) {
+   } else if (false && strcmp(interface, "zwp_linux_dmabuf_v1") == 0 && version >= 3) {
       dri2_dpy->wl_dmabuf =
          wl_registry_bind(registry, name, &zwp_linux_dmabuf_v1_interface,
                           MIN2(version, 3));
