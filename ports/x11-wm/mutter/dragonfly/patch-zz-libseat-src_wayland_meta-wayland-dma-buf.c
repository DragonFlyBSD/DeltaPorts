--- src/wayland/meta-wayland-dma-buf.c.orig
+++ src/wayland/meta-wayland-dma-buf.c
@@ -1151,6 +1151,12 @@
   if (!dma_buf)
     return NULL;
 
+  /* DragonFly dma-buf fds do not implement fence polling: poll() never
+   * reports readable, even for idle buffers, so waiting would stall
+   * forever. Skip the readiness wait and rely on implicit sync at
+   * sampling time, like wlroots does. */
+  return NULL;
+
   for (i = 0; i < META_WAYLAND_DMA_BUF_MAX_FDS; i++)
     {
       int fd = dma_buf->fds[i];
