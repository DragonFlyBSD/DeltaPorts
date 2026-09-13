--- sysdeps/freebsd/glibtop_machine.h.orig
+++ sysdeps/freebsd/glibtop_machine.h
@@ -26,7 +26,9 @@
 #include <nlist.h>
 #include <limits.h>
 #include <kvm.h>
+#ifndef __DragonFly__
 #include <sys/dkstat.h>
+#endif
 #include <time.h>
 #include <sys/user.h>
 #include <sys/types.h>
