diff --git a/src/egl/main/eglglobals.c b/src/egl/main/eglglobals.c
index 6811048bdf7..5eefbc9187b 100644
--- a/src/egl/main/eglglobals.c
+++ b/src/egl/main/eglglobals.c
@@ -136,7 +136,7 @@ _eglPointerIsDereferencable(void *p)
 {
    uintptr_t addr = (uintptr_t) p;
    const long page_size = getpagesize();
-#ifdef HAVE_MINCORE
+#if 0
    unsigned char valid = 0;
 
    if (p == NULL)
