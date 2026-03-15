with Arthexis.ORM;

package Apps.Templates.Models.Templates_Models is
   --  Schema objects owned by the Templates component app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create Templates component registry table.
end Apps.Templates.Models.Templates_Models;
