--- sysdeps/freebsd/procsignal.c.orig
+++ sysdeps/freebsd/procsignal.c
@@ -64,10 +64,17 @@
 		return;
 	}
 
+#ifdef __DragonFly__
+#define	PROC_SIGLIST	kp_siglist
+#define	PROC_SIGMASK	kp_lwp.kl_sigmask
+#define	PROC_SIGIGNORE	kp_sigignore
+#define	PROC_SIGCATCH	kp_sigcatch
+#else
 #define	PROC_SIGLIST	ki_siglist
 #define	PROC_SIGMASK	ki_sigmask
 #define	PROC_SIGIGNORE	ki_sigignore
 #define	PROC_SIGCATCH	ki_sigcatch
+#endif
 
 	/* signal: mask of pending signals.
 	 *         pinfo [0].kp_proc.p_siglist
