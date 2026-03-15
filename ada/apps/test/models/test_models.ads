with Arthexis.ORM;

package Apps.Test.Models.Test_Models is
   --  Schema objects owned by the optional Test app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create optional test toggles used by products.
end Apps.Test.Models.Test_Models;
