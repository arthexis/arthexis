with Arthexis.ORM;

package Apps.Models.Models.Models_Models is
   --  Schema objects owned by the Models component app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create Models component registry table.
end Apps.Models.Models.Models_Models;
