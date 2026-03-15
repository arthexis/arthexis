with Arthexis.ORM;

package Apps.Templates.Functions.Templates_Functions is
   --  SQL-callable function registrations for the Templates component app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register Templates component helper views.
end Apps.Templates.Functions.Templates_Functions;
