with Arthexis.ORM;

package Apps.Model.Models.Model_Models is
   --  Schema objects owned by the Model backbone app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create model matrix tables and indexes.
end Apps.Model.Models.Model_Models;
