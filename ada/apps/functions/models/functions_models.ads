with Arthexis.ORM;

package Apps.Functions.Models.Functions_Models is
   --  Schema objects owned by the Functions component app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create Functions component registry table.
end Apps.Functions.Models.Functions_Models;
