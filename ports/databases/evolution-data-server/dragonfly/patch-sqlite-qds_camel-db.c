--- src/camel/camel-db.c.orig
+++ src/camel/camel-db.c
@@ -870,5 +870,9 @@
 	cdb->priv->db = db;
 	cdb->priv->filename = g_strdup (filename);
 	d (g_print ("\nDatabase successfully opened  \n"));
 
+	/*Enable Double-Quoted*/
+	sqlite3_db_config(db, SQLITE_DBCONFIG_DQS_DDL, 1, (void*)0);
+	sqlite3_db_config(db, SQLITE_DBCONFIG_DQS_DML, 1, (void*)0);
+
 	sqlite3_create_function (db, "CAMELCOMPAREDATE", 2, SQLITE_UTF8 | SQLITE_DETERMINISTIC, NULL, cdb_camel_compare_date_func, NULL, NULL);
