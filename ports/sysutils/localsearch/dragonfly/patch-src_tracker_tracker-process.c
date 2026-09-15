--- src/tracker/tracker-process.c.orig
+++ src/tracker/tracker-process.c
@@ -26,6 +26,13 @@
 #include <glib.h>
 #include <glib/gi18n.h>
 
+#ifdef __DragonFly__
+#include <sys/types.h>
+#include <sys/sysctl.h>
+#include <sys/kinfo.h>
+#include <unistd.h>
+#endif
+
 #ifdef __OpenBSD__
 #include <sys/types.h>
 #include <sys/sysctl.h>
@@ -152,6 +159,44 @@
 	return (pid_t) process_id;
 }
 
+#ifdef __DragonFly__
+/*
+ * DragonFly D-Bus credentials expose the peer UID but not its PID, so
+ * GetConnectionUnixProcessID returns zero for a running session service.
+ * This fallback is only used after the service name has been resolved.
+ */
+static pid_t
+find_pid_for_command (const gchar *command)
+{
+	int mib[4] = { CTL_KERN, KERN_PROC, KERN_PROC_UID, getuid () };
+	struct kinfo_proc *processes;
+	size_t length;
+	size_t n_processes;
+	size_t i;
+	pid_t pid = -1;
+
+	if (sysctl (mib, 4, NULL, &length, NULL, 0) < 0 || length == 0)
+		return -1;
+
+	processes = g_malloc (length);
+	if (sysctl (mib, 4, processes, &length, NULL, 0) < 0) {
+		g_free (processes);
+		return -1;
+	}
+
+	n_processes = length / sizeof (*processes);
+	for (i = 0; i < n_processes; i++) {
+		if (g_strcmp0 (processes[i].kp_comm, command) == 0) {
+			pid = processes[i].kp_pid;
+			break;
+		}
+	}
+
+	g_free (processes);
+	return pid;
+}
+#endif
+
 GSList *
 tracker_process_find_all (void)
 {
@@ -169,6 +214,13 @@
 			data = process_data_new (command, miner_fs);
 			processes = g_slist_prepend (processes, data);
 		}
+	} else if (miner_fs == 0) {
+#ifdef __DragonFly__
+		miner_fs = find_pid_for_command ("localsearch-3");
+		if (miner_fs > 0)
+			processes = g_slist_prepend (processes,
+						  process_data_new (g_strdup ("localsearch-3"), miner_fs));
+#endif
 	}
 
 	miner_rss = get_pid_for_service (connection, "org.freedesktop.Tracker3.Miner.RSS");
@@ -178,6 +230,13 @@
 			data = process_data_new (command, miner_rss);
 			processes = g_slist_prepend (processes, data);
 		}
+	} else if (miner_rss == 0) {
+#ifdef __DragonFly__
+		miner_rss = find_pid_for_command ("tracker-miner-rss-3");
+		if (miner_rss > 0)
+			processes = g_slist_prepend (processes,
+						  process_data_new (g_strdup ("tracker-miner-rss-3"), miner_rss));
+#endif
 	}
 
 	g_object_unref (connection);
