--- Modules/usbview/usbdevices.h.intermediate	2018-05-01 12:46:03 UTC
+++ Modules/usbview/usbdevices.h
@@ -15,7 +15,7 @@
 #include <QString>
 
 #if defined(__DragonFly__)
-#include <bus/usb/usb.h>
+#include <bus/u4b/usb.h>
 #include <QStringList>
 #elif defined(Q_OS_FREEBSD) || defined(Q_OS_NETBSD)
 #include <sys/param.h>
