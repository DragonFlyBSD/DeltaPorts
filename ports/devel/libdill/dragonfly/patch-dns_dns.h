--- dns/dns.h.orig
+++ dns/dns.h
@@ -149,9 +149,8 @@
 #define DNS_PRAGMA_QUIET _Pragma("GCC diagnostic ignored \"-Woverride-init\"")
 #define DNS_PRAGMA_POP _Pragma("GCC diagnostic pop")
 
-/* GCC parses the _Pragma operator less elegantly than clang. */
-#define dns_quietinit(...) \
-	__extension__ ({ DNS_PRAGMA_PUSH DNS_PRAGMA_QUIET __VA_ARGS__; DNS_PRAGMA_POP })
+/* GCC rejects & on the statement expression: no pragmas for it. */
+#define dns_quietinit(...) __VA_ARGS__
 #else
 #define DNS_PRAGMA_PUSH
 #define DNS_PRAGMA_QUIET
