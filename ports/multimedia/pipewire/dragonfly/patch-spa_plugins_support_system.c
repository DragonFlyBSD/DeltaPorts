--- spa/plugins/support/system.c.orig
+++ spa/plugins/support/system.c
@@ -9,6 +9,10 @@
 #include <stdlib.h>
 #include <stdio.h>
 #include <sys/epoll.h>
+#ifdef __DragonFly__
+#include <sys/stat.h>
+#include <fcntl.h>
+#endif
 #include <sys/ioctl.h>
 #include <sys/timerfd.h>
 #include <sys/eventfd.h>
@@ -188,9 +192,42 @@
 }
 
 /* events */
+#ifdef __DragonFly__
+/* DragonFly cannot pass pipe or kqueue descriptors over SCM_RIGHTS (the
+ * kernel's unp_internalize only accepts vnode-backed files), so the
+ * epoll-shim pipe-based eventfd cannot cross the protocol-native socket.
+ * Emulate eventfd with an unlinked vnode FIFO opened O_RDWR: a single fd,
+ * readable and writable from both processes, and transferable. */
 static int impl_eventfd_create(void *object, int flags)
 {
 	struct impl *impl = object;
+	char path[64];
+	static int serial;
+	int fd, fl = O_RDWR;
+
+	if (flags & SPA_FD_CLOEXEC)
+		fl |= O_CLOEXEC;
+	if (flags & SPA_FD_NONBLOCK)
+		fl |= O_NONBLOCK;
+
+	snprintf(path, sizeof(path), "/tmp/.spa-eventfd-%d-%d",
+			(int) getpid(), __sync_fetch_and_add(&serial, 1));
+	if (mkfifo(path, 0600) < 0)
+		return -errno;
+	fd = open(path, fl);
+	if (fd < 0) {
+		int res = -errno;
+		unlink(path);
+		return res;
+	}
+	unlink(path);
+	spa_log_debug(impl->log, "%p: new fifo-eventfd fd:%d", impl, fd);
+	return fd;
+}
+#else
+static int impl_eventfd_create(void *object, int flags)
+{
+	struct impl *impl = object;
 	int fl = 0, res, err;
 	if (flags & SPA_FD_CLOEXEC)
 		fl |= EFD_CLOEXEC;
@@ -203,6 +240,7 @@
 	spa_log_debug(impl->log, "%p: new fd:%d", impl, res);
 	return res < 0 ? err : res;
 }
+#endif
 
 static int impl_eventfd_write(void *object, int fd, uint64_t count)
 {
