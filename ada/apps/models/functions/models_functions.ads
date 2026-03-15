with Arthexis.ORM;

package Apps.Models.Functions.Models_Functions is
   --  SQL-callable function registrations for the Models component app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register Models component helper views.
end Apps.Models.Functions.Models_Functions;
