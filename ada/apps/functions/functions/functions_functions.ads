with Arthexis.ORM;

package Apps.Functions.Functions.Functions_Functions is
   --  SQL-callable function registrations for the Functions component app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register Functions component helper views.
end Apps.Functions.Functions.Functions_Functions;
