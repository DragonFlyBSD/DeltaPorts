diff --git a/src/egl/drivers/dri2/egl_dri2.c b/src/egl/drivers/dri2/egl_dri2.c
index 2e51d28a8b9..95ee2f3a7b1 100644
--- a/src/egl/drivers/dri2/egl_dri2.c
+++ b/src/egl/drivers/dri2/egl_dri2.c
@@ -1005,8 +1005,8 @@ dri2_setup_screen(_EGLDisplay *disp)
 #ifdef HAVE_LIBDRM
       if (dri2_dpy->image->base.version >= 8 &&
           dri2_dpy->image->createImageFromDmaBufs) {
-         disp->Extensions.EXT_image_dma_buf_import = EGL_TRUE;
-         disp->Extensions.EXT_image_dma_buf_import_modifiers = EGL_TRUE;
+         disp->Extensions.EXT_image_dma_buf_import = EGL_FALSE;
+         disp->Extensions.EXT_image_dma_buf_import_modifiers = EGL_FALSE;
       }
 #endif
    }
