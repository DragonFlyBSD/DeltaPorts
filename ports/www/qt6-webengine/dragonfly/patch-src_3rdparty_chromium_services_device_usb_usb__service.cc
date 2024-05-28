--- src/3rdparty/chromium/services/device/usb/usb_service.cc.intermediate	2024-05-27 23:30:18 UTC
+++ src/3rdparty/chromium/services/device/usb/usb_service.cc
@@ -31,7 +31,7 @@
 #include "services/device/usb/usb_service_win.h"
 #elif BUILDFLAG(IS_OPENBSD)
 #include "services/device/usb/usb_service_impl.h"
-#elif BUILDFLAG(IS_FREEBSD)
+#elif BUILDFLAG(IS_FREEBSD) || BUILDFLAG(IS_DRAGONFLY)
 #include "services/device/usb/usb_service_fake.h"
 #endif
 
