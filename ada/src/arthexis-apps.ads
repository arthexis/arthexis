with Arthexis.ORM;

package Arthexis.Apps is
   """App registry that mirrors Django-style app initialization order."""

   procedure Install_All (Conn : in out Arthexis.ORM.Database_Connection);
   --  Install schema, functions, and triggers for every Ada app.
end Arthexis.Apps;
