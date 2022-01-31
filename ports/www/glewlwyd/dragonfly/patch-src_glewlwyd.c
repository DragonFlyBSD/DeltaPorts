--- src/glewlwyd.c.orig	2020-01-16 18:19:57 UTC
+++ src/glewlwyd.c
@@ -1010,11 +1010,15 @@ int build_config_from_file(struct config
             config_destroy(&cfg);
             ret = G_ERROR_PARAM;
           } else {
+#ifdef _HOEL_SQLITE
             if (h_exec_query_sqlite(config->conn, "PRAGMA foreign_keys = ON;") != H_OK) {
               y_log_message(Y_LOG_LEVEL_ERROR, "Error executing sqlite3 query 'PRAGMA foreign_keys = ON;'");
               config_destroy(&cfg);
               ret = G_ERROR_PARAM;
             }
+#else
+              ret = G_ERROR_PARAM;
+#endif
           }
         } else {
           config_destroy(&cfg);
@@ -1033,11 +1037,15 @@ int build_config_from_file(struct config
           config_destroy(&cfg);
           ret = G_ERROR_PARAM;
         } else {
+#ifdef _HOEL_MARIADB
           if (h_execute_query_mariadb(config->conn, "SET sql_mode='PIPES_AS_CONCAT';", NULL) != H_OK) {
             y_log_message(Y_LOG_LEVEL_ERROR, "Error executing mariadb query 'SET sql_mode='PIPES_AS_CONCAT';'");
             config_destroy(&cfg);
             ret = G_ERROR_PARAM;
           }
+#else
+            ret = G_ERROR_PARAM;
+#endif
         }
       } else if (0 == o_strcmp(str_value, "postgre")) {
         config_setting_lookup_string(database, "conninfo", &str_value_2);
@@ -1393,10 +1401,14 @@ int build_config_from_env(struct config_
         fprintf(stderr, "Error opening sqlite database '%s'\n", getenv(GLEWLWYD_ENV_DATABASE_SQLITE3_PATH));
         ret = G_ERROR_PARAM;
       } else {
+#ifdef _HOEL_SQLITE
         if (h_exec_query_sqlite(config->conn, "PRAGMA foreign_keys = ON;") != H_OK) {
           y_log_message(Y_LOG_LEVEL_ERROR, "Error executing sqlite3 query 'PRAGMA foreign_keys = ON;'");
           ret = G_ERROR_PARAM;
         }
+#else
+          ret = G_ERROR_PARAM;
+#endif
       }
     } else if (0 == o_strcmp(value, "mariadb")) {
       lvalue = strtol(getenv(GLEWLWYD_ENV_DATABASE_MARIADB_PORT), &endptr, 10);
@@ -1405,10 +1417,14 @@ int build_config_from_env(struct config_
           fprintf(stderr, "Error opening mariadb database '%s'\n", getenv(GLEWLWYD_ENV_DATABASE_MARIADB_DBNAME));
           ret = G_ERROR_PARAM;
         } else {
+#ifdef _HOEL_MARIADB
           if (h_execute_query_mariadb(config->conn, "SET sql_mode='PIPES_AS_CONCAT';", NULL) != H_OK) {
             y_log_message(Y_LOG_LEVEL_ERROR, "Error executing mariadb query 'SET sql_mode='PIPES_AS_CONCAT';'");
             ret = G_ERROR_PARAM;
           }
+#else
+            ret = G_ERROR_PARAM;
+#endif
         }
       }
     } else if (0 == o_strcmp(value, "postgre")) {
