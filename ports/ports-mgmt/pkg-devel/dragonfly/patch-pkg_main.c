--- pkg/main.c.orig	2013-11-19 18:52:23.000000000 +0000
+++ pkg/main.c
@@ -538,13 +538,12 @@ main(int argc, char **argv)
 	int newargc;
 	Tokenizer *t = NULL;
 	struct sbuf *newcmd;
-	int j, cmdargc;
+	int j;
 
 	/* Set stdout unbuffered */
 	setvbuf(stdout, NULL, _IONBF, 0);
 
 	cmdargv = argv;
-	cmdargc = argc;
 
 	if (argc < 2)
 		usage(NULL, NULL);
@@ -696,8 +695,11 @@ main(int argc, char **argv)
 			}
 			sbuf_done(newcmd);
 			t = tok_init(NULL);
+#pragma GCC diagnostic push
+#pragma GCC diagnostic ignored "-Wcast-qual"
 			if (tok_str(t, sbuf_data(newcmd), &newargc, (const char ***)&newargv) != 0)
 				errx(EX_CONFIG, "Invalid alias: %s", alias_value);
+#pragma GCC diagnostic pop
 			sbuf_delete(newcmd);
 			break;
 		}
