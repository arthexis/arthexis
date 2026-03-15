with Arthexis.ORM;

package Apps.Views.Models.Views_Models is
   --  Schema objects owned by the Views component app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create Views component registry table.
end Apps.Views.Models.Views_Models;
