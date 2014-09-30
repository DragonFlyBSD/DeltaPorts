--- src/main.c.orig	2014-09-19 17:02:55 UTC
+++ src/main.c
@@ -149,7 +149,7 @@ static void set_signal_handlers(void)
 	sigaction(SIGUSR2, &sa, NULL);
 }
 
-#if defined(__FreeBSD__) || defined(__APPLE__)
+#if defined(__FreeBSD__) || defined(__APPLE__) || defined(__DragonFly__)
 static int ppoll(struct pollfd *fds, nfds_t nfds, const struct timespec *timeout, const sigset_t *sigmask)
 {
 	int ready;
