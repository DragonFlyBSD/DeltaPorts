--- solenv/gbuild/Deliver.mk.orig	2013-05-02 15:55:29.000000000 +0000
+++ solenv/gbuild/Deliver.mk
@@ -60,7 +60,7 @@ endif
 endef
 
 define gb_Deliver__deliver
-$(if $(gb_Deliver_CLEARONDELIVER),rm -f $(2) &&) $(if $(gb_Deliver_HARDLINK),ln,cp -P -f) $(1) $(2) && touch $(if $(filter-out MACOSX,$(OS_FOR_BUILD)),--no-dereference) -r $(1) $(2)
+$(if $(gb_Deliver_CLEARONDELIVER),rm -f $(2) &&) $(if $(gb_Deliver_HARDLINK),ln,cp -P -f) $(1) $(2) && touch $(if $(filter-out MACOSX DRAGONFLY,$(OS_FOR_BUILD)),--no-dereference) -r $(1) $(2)
 endef
 
 ifneq ($(strip $(gb_Deliver_GNUCOPY)),)
