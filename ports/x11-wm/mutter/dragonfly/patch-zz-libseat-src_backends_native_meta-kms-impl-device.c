--- src/backends/native/meta-kms-impl-device.c.orig
+++ src/backends/native/meta-kms-impl-device.c
@@ -22,7 +22,6 @@
 #include <errno.h>
 #include <fcntl.h>
 #include <glib/gstdio.h>
-#include <linux/dma-buf.h>
 #include <poll.h>
 #include <sys/ioctl.h>
 #include <sys/timerfd.h>
