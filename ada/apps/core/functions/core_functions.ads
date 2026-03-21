with Arthexis.ORM;

package Apps.Core.Functions.Core_Functions is
   --  SQL-callable function registrations for the Core app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register Core SQL functions.
end Apps.Core.Functions.Core_Functions;
