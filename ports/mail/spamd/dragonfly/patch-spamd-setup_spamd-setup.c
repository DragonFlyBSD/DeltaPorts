--- spamd-setup/spamd-setup.c.orig	2010-10-30 22:08:20.000000000 +0000
+++ spamd-setup/spamd-setup.c
@@ -98,7 +98,7 @@ int		 configure_spamd(u_short, char *, c
 int		 configure_pf(struct cidr *);
 int		 getlist(char **, char *, struct blacklist *, struct blacklist *);
 __dead void	 usage(void);
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 int		  configure_ipfw(struct cidr *);
 #endif
 
