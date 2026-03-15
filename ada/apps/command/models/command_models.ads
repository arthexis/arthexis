with Arthexis.ORM;

package Apps.Command.Models.Command_Models is
   --  Schema objects owned by the Command app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create command entry-point tables and indexes.
end Apps.Command.Models.Command_Models;
