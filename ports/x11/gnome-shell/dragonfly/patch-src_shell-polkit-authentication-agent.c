--- src/shell-polkit-authentication-agent.c.orig
+++ src/shell-polkit-authentication-agent.c
@@ -92,6 +92,12 @@
 shell_polkit_authentication_agent_register (ShellPolkitAuthenticationAgent *agent,
                                             GError                        **error_out)
 {
+#ifdef __DragonFly__
+  /* No suitable session backend yet; restore registration with native login1.
+   * This disables only the desktop agent, not polkit authorization checks.
+   */
+  return;
+#endif
   GError *error = NULL;
   PolkitSubject *subject;
 
