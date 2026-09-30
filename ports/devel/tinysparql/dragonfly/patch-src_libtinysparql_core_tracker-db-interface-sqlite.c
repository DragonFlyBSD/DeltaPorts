--- src/libtinysparql/core/tracker-db-interface-sqlite.c.orig
+++ src/libtinysparql/core/tracker-db-interface-sqlite.c
@@ -1904,6 +1904,31 @@
 		                         db_interface->filename? db_interface->filename : "memory"));
 	}
 
+#ifdef SQLITE_DBCONFIG_DQS_DML
+	/* Generated queries retain SQLite's legacy double-quoted literals. */
+	result = sqlite3_db_config (db_interface->db, SQLITE_DBCONFIG_DQS_DML, 1, NULL);
+	if (result != SQLITE_OK) {
+		g_set_error (error,
+		             TRACKER_DB_INTERFACE_ERROR,
+		             TRACKER_DB_OPEN_ERROR,
+		             "Could not enable SQLite DQS for DML: %s",
+		             sqlite3_errstr (result));
+		return;
+	}
+#endif
+
+#ifdef SQLITE_DBCONFIG_DQS_DDL
+	result = sqlite3_db_config (db_interface->db, SQLITE_DBCONFIG_DQS_DDL, 1, NULL);
+	if (result != SQLITE_OK) {
+		g_set_error (error,
+		             TRACKER_DB_INTERFACE_ERROR,
+		             TRACKER_DB_OPEN_ERROR,
+		             "Could not enable SQLite DQS for DDL: %s",
+		             sqlite3_errstr (result));
+		return;
+	}
+#endif
+
 	/* Set our unicode collation function */
 	tracker_db_interface_sqlite_reset_collator (db_interface);
 
